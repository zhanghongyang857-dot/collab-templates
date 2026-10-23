/*
 * 御前看板启动器（应用包主程序）
 *
 * 两种模式：
 *  1. 常驻：读取 ~/Library/Application Support/ai-company-board/config，
 *     反复启动 tools/board/server.py；服务退出 3 秒后重启。
 *  2. 权限探针：看到 ~/Library/Logs/ai-company-board/probe.request 就只检查
 *     ~/Documents 是否可读，把结果写到 tcc-probe.txt 后退出。install 用 open
 *     启动应用做这一步，系统会问一次「允许访问文稿」，授权记在「御前看板」上。
 *
 * 应用包里只放这个启动器；改 server.py 不需要重建应用。配置和请求都放在应用外，
 * 所以 open 不传参数也能工作。
 * 只用 POSIX / macOS 自带接口，无第三方依赖。
 */
#define _DARWIN_C_SOURCE

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define RESTART_SECONDS 3
#define LOG_REL "Library/Logs/ai-company-board"
#define CONFIG_REL "Library/Application Support/ai-company-board/config"

static volatile sig_atomic_t g_stop = 0;
static volatile pid_t g_child = -1;

typedef struct {
    char python[PATH_MAX];
    char server[PATH_MAX];
    char root[PATH_MAX];
    char port[16];
    char now_dir[PATH_MAX];
    char log_dir[PATH_MAX];
} Config;

static void logmsg(const char *fmt, ...) {
    char ts[32];
    time_t now = time(NULL);
    struct tm tmv;
    localtime_r(&now, &tmv);
    strftime(ts, sizeof ts, "%Y-%m-%d %H:%M:%S", &tmv);

    fprintf(stderr, "[%s] ", ts);
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    fflush(stderr);
}

static void on_signal(int sig) {
    (void)sig;
    g_stop = 1;
    pid_t p = g_child;
    if (p > 0) {
        kill(-p, SIGTERM);
        kill(p, SIGTERM);
    }
}

static void home_path(char *out, size_t out_len, const char *rel) {
    const char *home = getenv("HOME");
    snprintf(out, out_len, "%s/%s", (home && *home) ? home : "/", rel);
}

/* dir 为空时检查 ~/Documents（触发系统授权询问）；非空时只检查该目录（测试用）。 */
static int probe_dir(const char *dir, char *msg, size_t msg_len) {
    char path[PATH_MAX];
    if (dir && *dir) {
        snprintf(path, sizeof path, "%s", dir);
    } else {
        const char *home = getenv("HOME");
        if (!home || !*home) {
            snprintf(msg, msg_len, "HOME 未设置");
            return 0;
        }
        snprintf(path, sizeof path, "%s/Documents", home);
    }

    DIR *d = opendir(path);
    if (!d) {
        snprintf(msg, msg_len, "%s：%s", path, strerror(errno));
        return 0;
    }
    closedir(d);
    snprintf(msg, msg_len, "%s 可读", path);
    return 1;
}

static int write_probe_result(const char *result_path, int ok, const char *msg) {
    if (!result_path || !*result_path) {
        return 0;
    }
    FILE *f = fopen(result_path, "w");
    if (!f) {
        logmsg("无法写结果文件 %s：%s", result_path, strerror(errno));
        return -1;
    }
    fprintf(f, "%s\n", ok ? "ok" : "denied");
    if (!ok) {
        fprintf(f, "%s\n", msg);
    }
    fclose(f);
    return 0;
}

static int run_probe(const char *result_path, const char *probe_path) {
    char msg[PATH_MAX + 256];
    int ok = probe_dir(probe_path, msg, sizeof msg);
    write_probe_result(result_path, ok, msg);
    logmsg("%s：%s", ok ? "文稿权限正常" : "文稿权限不可用", msg);
    return ok ? 0 : 1;
}

static const char *opt(int argc, char **argv, const char *name, const char *def) {
    for (int i = 1; i + 1 < argc; i++) {
        if (strcmp(argv[i], name) == 0) {
            return argv[i + 1];
        }
    }
    return def;
}

static int flag(int argc, char **argv, const char *name) {
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], name) == 0) {
            return 1;
        }
    }
    return 0;
}

static char *trim(char *s) {
    while (*s && isspace((unsigned char)*s)) {
        s++;
    }
    char *e = s + strlen(s);
    while (e > s && isspace((unsigned char)e[-1])) {
        *--e = '\0';
    }
    return s;
}

static void set_str(char *dst, size_t dst_len, const char *src) {
    snprintf(dst, dst_len, "%s", src);
}

static int load_config(const char *path, Config *c) {
    memset(c, 0, sizeof *c);
    set_str(c->python, sizeof c->python, "/usr/bin/python3");
    set_str(c->port, sizeof c->port, "3100");

    FILE *f = fopen(path, "r");
    if (!f) {
        logmsg("无法读取配置 %s：%s", path, strerror(errno));
        return 0;
    }

    char line[PATH_MAX * 2];
    while (fgets(line, sizeof line, f)) {
        char *s = trim(line);
        if (!*s || *s == '#') {
            continue;
        }
        char *eq = strchr(s, '=');
        if (!eq) {
            continue;
        }
        *eq = '\0';
        char *k = trim(s);
        char *v = trim(eq + 1);

        if (strcmp(k, "PYTHON") == 0) {
            set_str(c->python, sizeof c->python, v);
        } else if (strcmp(k, "SERVER") == 0) {
            set_str(c->server, sizeof c->server, v);
        } else if (strcmp(k, "ROOT") == 0) {
            set_str(c->root, sizeof c->root, v);
        } else if (strcmp(k, "PORT") == 0) {
            set_str(c->port, sizeof c->port, v);
        } else if (strcmp(k, "NOW_DIR") == 0) {
            set_str(c->now_dir, sizeof c->now_dir, v);
        } else if (strcmp(k, "LOG_DIR") == 0) {
            set_str(c->log_dir, sizeof c->log_dir, v);
        }
    }
    fclose(f);

    if (!c->server[0] || !c->log_dir[0]) {
        logmsg("配置缺少 SERVER 或 LOG_DIR：%s", path);
        return 0;
    }
    if (!c->root[0]) {
        set_str(c->root, sizeof c->root, c->server);
        for (int i = 0; i < 3; i++) {
            char *slash = strrchr(c->root, '/');
            if (!slash) {
                break;
            }
            *slash = '\0';
        }
    }
    return 1;
}

int main(int argc, char **argv) {
    /* 测试/调试用：直接执行应用内二进制时，可显式指定探针目录和结果文件。 */
    if (flag(argc, argv, "--probe")) {
        return run_probe(opt(argc, argv, "--probe-result", NULL),
                         opt(argc, argv, "--probe-dir", NULL));
    }

    /* install 用 open 启动应用时只留下一个请求文件，不依赖 open 传参。 */
    char probe_request[PATH_MAX], probe_result[PATH_MAX];
    home_path(probe_request, sizeof probe_request, LOG_REL "/probe.request");
    home_path(probe_result, sizeof probe_result, LOG_REL "/tcc-probe.txt");
    if (access(probe_request, F_OK) == 0) {
        int rc = run_probe(probe_result, NULL);
        unlink(probe_request);
        return rc;
    }

    char config_default[PATH_MAX];
    const char *config_path = opt(argc, argv, "--config", NULL);
    if (!config_path || !*config_path) {
        home_path(config_default, sizeof config_default, CONFIG_REL);
        config_path = config_default;
    }

    Config c;
    if (!load_config(config_path, &c)) {
        return 2;
    }

    char out_path[PATH_MAX], err_path[PATH_MAX];
    snprintf(out_path, sizeof out_path, "%s/board.log", c.log_dir);
    snprintf(err_path, sizeof err_path, "%s/board.err.log", c.log_dir);
    mkdir(c.log_dir, 0755);  /* 已存在时忽略 EEXIST */

    int out_fd = open(out_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    int err_fd = open(err_path, O_WRONLY | O_CREAT | O_APPEND, 0644);

    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);
    signal(SIGHUP, SIG_IGN);
    signal(SIGPIPE, SIG_IGN);

    if (chdir(c.root) != 0) {
        logmsg("无法进入工作目录 %s：%s", c.root, strerror(errno));
    }

    char msg[PATH_MAX + 256];
    int allowed = probe_dir(NULL, msg, sizeof msg);
    logmsg("%s：%s", allowed ? "文稿权限正常" : "文稿权限不可用（会每 3 秒重试）", msg);
    logmsg("启动器：端口 %s，服务 %s，日志 %s", c.port, c.server, out_path);

    while (!g_stop) {
        pid_t pid = fork();
        if (pid < 0) {
            logmsg("fork 失败：%s，%d 秒后重试", strerror(errno), RESTART_SECONDS);
            sleep(RESTART_SECONDS);
            continue;
        }
        if (pid == 0) {
            setpgid(0, 0);
            if (out_fd >= 0) {
                dup2(out_fd, STDOUT_FILENO);
            }
            if (err_fd >= 0) {
                dup2(err_fd, STDERR_FILENO);
            }
            setenv("PYTHONUNBUFFERED", "1", 1);
            setenv("PYTHONIOENCODING", "utf-8", 1);

            char *args[10];
            int n = 0;
            args[n++] = c.python;
            args[n++] = c.server;
            if (c.port[0]) {
                args[n++] = "--port";
                args[n++] = c.port;
            }
            if (c.now_dir[0]) {
                args[n++] = "--now-dir";
                args[n++] = c.now_dir;
            }
            args[n] = NULL;

            execv(c.python, args);
            fprintf(stderr, "无法启动 %s：%s\n", c.python, strerror(errno));
            _exit(127);
        }

        g_child = pid;
        setpgid(pid, pid);  /* 与子进程里的 setpgid 竞争，忽略失败 */

        int status = 0;
        pid_t w;
        do {
            w = waitpid(pid, &status, 0);
        } while (w < 0 && errno == EINTR && !g_stop);
        g_child = -1;

        if (g_stop) {
            break;
        }
        if (WIFEXITED(status)) {
            logmsg("服务退出（代码 %d），%d 秒后重启", WEXITSTATUS(status), RESTART_SECONDS);
        } else if (WIFSIGNALED(status)) {
            logmsg("服务被信号 %d 终止，%d 秒后重启", WTERMSIG(status), RESTART_SECONDS);
        } else {
            logmsg("服务结束，%d 秒后重启", RESTART_SECONDS);
        }

        struct timespec ts = {RESTART_SECONDS, 0};
        while (nanosleep(&ts, &ts) < 0 && errno == EINTR && !g_stop) {
            /* 被信号打断时继续睡剩余时间；收到停止信号则退出 */
        }
    }

    if (out_fd >= 0) {
        close(out_fd);
    }
    if (err_fd >= 0) {
        close(err_fd);
    }
    logmsg("启动器退出");
    return 0;
}
