/* Multi-threaded test fixture for the Nova DAP server.
 *
 * This is a deliberately tiny pthread program used by
 * ``dap_multi_thread.py`` to exercise the DAP server's multi-thread
 * coordination paths (per-thread step / continue / pause, the
 * ``threads`` request, and stop-event attribution).
 *
 * NOVA itself does not yet emit OS threads from .nova source — the
 * runtime's concurrency primitives are coroutines (cooperative
 * scheduling on a single OS thread). That means a .nova binary
 * always presents as a single LWP to gdb, which is uninteresting
 * for testing the multi-thread DAP wire protocol. We use this C
 * fixture instead. The DAP server is language-agnostic at the gdb-MI
 * layer, so testing against pthreads exercises exactly the same
 * code paths that future thread-aware NOVA programs would.
 *
 * Build (manual; ``dap_multi_thread.py`` does this if ``gcc`` is
 * available, otherwise the test SKIPs):
 *
 *     gcc -g -O0 -pthread -o /tmp/nova_dap_multi_thread \
 *         tools/nova-dap/tests/fixtures/multi_thread.c
 *
 * Two worker threads each spin in a small loop with thread-local
 * state, then join. Each loop body has obvious source lines we can
 * set per-thread breakpoints on.
 */
#include <pthread.h>
#include <stdio.h>
#include <unistd.h>

/* Synchronization shared between the workers and main. The workers
 * wait on ``ready`` so they're definitely both spawned before either
 * starts looping — that makes the breakpoint-routing test
 * deterministic. */
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  ready_cv = PTHREAD_COND_INITIALIZER;
static int             ready = 0;

static void wait_for_ready(void) {
    pthread_mutex_lock(&lock);
    while (!ready) {
        pthread_cond_wait(&ready_cv, &lock);
    }
    pthread_mutex_unlock(&lock);
}

void *worker_a(void *arg) {
    (void)arg;
    int local_a = 100;
    int sum_a   = 0;
    wait_for_ready();
    for (int i = 0; i < 5; ++i) {
        local_a += 1;          /* WORKER_A_BP — see dap_multi_thread.py */
        sum_a   += local_a;
        usleep(5000);
    }
    return (void *)(long)sum_a;
}

void *worker_b(void *arg) {
    (void)arg;
    int local_b = 200;
    int sum_b   = 0;
    wait_for_ready();
    for (int i = 0; i < 5; ++i) {
        local_b += 2;          /* WORKER_B_BP — see dap_multi_thread.py */
        sum_b   += local_b;
        usleep(5000);
    }
    return (void *)(long)sum_b;
}

int main(void) {
    pthread_t ta, tb;
    pthread_create(&ta, NULL, worker_a, NULL);
    pthread_create(&tb, NULL, worker_b, NULL);
    /* Give gdb a moment to register both threads, then release. */
    usleep(50000);
    pthread_mutex_lock(&lock);
    ready = 1;
    pthread_cond_broadcast(&ready_cv);
    pthread_mutex_unlock(&lock);
    void *ra = NULL;
    void *rb = NULL;
    pthread_join(ta, &ra);
    pthread_join(tb, &rb);
    printf("done a=%ld b=%ld\n", (long)ra, (long)rb);
    return 0;
}
