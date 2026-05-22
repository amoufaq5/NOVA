// Nova LLM Bridge - thin C wrapper for llama.cpp
//
// This bridge exists because llama.cpp returns structs by value
// (e.g. llama_model_default_params(), llama_context_default_params())
// which cannot be called correctly from Nova's FFI. The System V ABI
// returns large structs via a hidden sret pointer, and Nova's ffi_call*
// functions only pass integer registers.
//
// Additionally, Nova's FFI passes all arguments in integer registers
// (rdi, rsi, rdx, rcx, r8, r9) with no XMM support for C calls.
// So all bridge functions accept floats as int64 bit patterns and
// convert them internally.
//
// Compile:
//   gcc -shared -fPIC -o libnovabridge.so src/runtime/llm_bridge.c -lllama
//
// The resulting libnovabridge.so must be in LD_LIBRARY_PATH or the
// same directory as the Nova executable.

#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include "llama.h"

// --- Utility: convert int64 bit pattern to float ---

static float bits_to_float(int64_t bits) {
    // Nova stores floats as IEEE 754 double bit patterns in 64-bit ints.
    // Convert: int64 -> double (reinterpret bits) -> float (truncate)
    double d;
    memcpy(&d, &bits, sizeof(d));
    return (float)d;
}

// --- Model Loading ---

// Returns a heap-allocated llama_model_params with defaults.
// Wraps the by-value return that Nova cannot handle.
struct llama_model_params* nova_model_default_params(void) {
    struct llama_model_params* p = malloc(sizeof(struct llama_model_params));
    if (!p) return NULL;
    *p = llama_model_default_params();
    return p;
}

// Set n_gpu_layers on a model params struct.
void nova_model_params_set_gpu(struct llama_model_params* p, int n_gpu_layers) {
    if (p) p->n_gpu_layers = n_gpu_layers;
}

// Load model from a GGUF file with the given params.
// Consumes (frees) the params struct.
struct llama_model* nova_load_model(const char* path, struct llama_model_params* params) {
    if (!path || !params) return NULL;
    struct llama_model* model = llama_load_model_from_file(path, *params);
    free(params);
    return model;
}

// --- Context Creation ---

// Returns a heap-allocated llama_context_params with defaults.
struct llama_context_params* nova_ctx_default_params(void) {
    struct llama_context_params* p = malloc(sizeof(struct llama_context_params));
    if (!p) return NULL;
    *p = llama_context_default_params();
    return p;
}

// Configure context params: context size, batch size, thread count.
void nova_ctx_params_set(struct llama_context_params* p, int n_ctx, int n_batch, int n_threads) {
    if (!p) return;
    p->n_ctx = n_ctx;
    p->n_batch = n_batch;
    p->n_threads = n_threads;
    p->n_threads_batch = n_threads;
}

// Create a context from a model with the given params.
// Consumes (frees) the params struct.
struct llama_context* nova_create_context(struct llama_model* model, struct llama_context_params* params) {
    if (!model || !params) return NULL;
    struct llama_context* ctx = llama_new_context_with_model(model, *params);
    free(params);
    return ctx;
}

// --- Tokenization ---

// Tokenize text into token array. Returns number of tokens (negative on truncation).
int nova_tokenize(struct llama_model* model, const char* text, int text_len,
                  int32_t* tokens, int max_tokens) {
    if (!model || !text || !tokens) return 0;
    return llama_tokenize(model, text, text_len, tokens, max_tokens, true, false);
}

// --- Batch Operations ---

// Create a batch and populate it with prompt tokens.
// Sets logits=1 only for the last token (needed for sampling).
struct llama_batch* nova_batch_create(int32_t* tokens, int n_tokens) {
    if (!tokens || n_tokens <= 0) return NULL;
    struct llama_batch* batch = malloc(sizeof(struct llama_batch));
    if (!batch) return NULL;
    *batch = llama_batch_init(n_tokens + 64, 0, 1);
    for (int i = 0; i < n_tokens; i++) {
        batch->token[i] = tokens[i];
        batch->pos[i] = i;
        batch->n_seq_id[i] = 1;
        batch->seq_id[i][0] = 0;
        batch->logits[i] = 0;
    }
    batch->logits[n_tokens - 1] = 1;
    batch->n_tokens = n_tokens;
    return batch;
}

// Run the forward pass (decode) on a batch.
int nova_decode(struct llama_context* ctx, struct llama_batch* batch) {
    if (!ctx || !batch) return -1;
    return llama_decode(ctx, *batch);
}

// Add a single token to the batch for next-token generation.
void nova_batch_add_token(struct llama_batch* batch, int32_t token, int pos) {
    if (!batch) return;
    int n = batch->n_tokens;
    batch->token[n] = token;
    batch->pos[n] = pos;
    batch->n_seq_id[n] = 1;
    batch->seq_id[n][0] = 0;
    batch->logits[n] = 1;
    batch->n_tokens = n + 1;
}

// Clear batch for reuse (reset token count to 0).
void nova_batch_clear(struct llama_batch* batch) {
    if (batch) batch->n_tokens = 0;
}

// Free a batch and its heap wrapper.
void nova_batch_free(struct llama_batch* batch) {
    if (!batch) return;
    llama_batch_free(*batch);
    free(batch);
}

// --- Sampling ---

// Create a sampler chain. temp_bits and top_p_bits are IEEE 754 double
// bit patterns packed into int64, because Nova FFI cannot pass floats.
// top_k is a plain integer.
struct llama_sampler* nova_sampler_create(int64_t temp_bits, int top_k, int64_t top_p_bits) {
    float temp = bits_to_float(temp_bits);
    float top_p = bits_to_float(top_p_bits);

    struct llama_sampler* smpl = llama_sampler_chain_init(
        llama_sampler_chain_default_params());

    if (temp <= 0.0f) {
        llama_sampler_chain_add(smpl, llama_sampler_init_greedy());
    } else {
        llama_sampler_chain_add(smpl, llama_sampler_init_top_k(top_k));
        llama_sampler_chain_add(smpl, llama_sampler_init_top_p(top_p, 1));
        llama_sampler_chain_add(smpl, llama_sampler_init_temp(temp));
        llama_sampler_chain_add(smpl, llama_sampler_init_dist(0));
    }
    return smpl;
}

// Sample the next token from the context at the given index.
// idx=-1 means the last logit position.
int32_t nova_sample(struct llama_sampler* smpl, struct llama_context* ctx, int idx) {
    if (!smpl || !ctx) return 0;
    return llama_sampler_sample(smpl, ctx, idx);
}

// Free the sampler chain.
void nova_sampler_free(struct llama_sampler* smpl) {
    if (smpl) llama_sampler_free(smpl);
}

// --- Token to text ---

// Convert a token ID to its text representation.
// Returns the number of bytes written to buf.
int nova_token_to_str(struct llama_model* model, int32_t token, char* buf, int buf_size) {
    if (!model || !buf || buf_size <= 0) return 0;
    return llama_token_to_piece(model, token, buf, buf_size, 0, true);
}

// Check if a token is end-of-generation (EOS/EOT).
int nova_is_eos(struct llama_model* model, int32_t token) {
    if (!model) return 1;
    return llama_token_is_eog(model, token);
}

// --- Cleanup ---

void nova_free_model(struct llama_model* model) {
    if (model) llama_free_model(model);
}

void nova_free_context(struct llama_context* ctx) {
    if (ctx) llama_free(ctx);
}

// --- High-level generate function ---
//
// Generate text from a prompt. All float parameters are passed as
// int64 IEEE 754 double bit patterns (Nova's internal float representation).
//
// Parameters:
//   model          - loaded model pointer
//   prompt         - prompt string
//   prompt_len     - byte length of prompt
//   output_buf     - pre-allocated output buffer
//   output_buf_size - size of output buffer
//   max_tokens     - maximum tokens to generate
//   temp_bits      - temperature as int64 double bits (use to_float() in Nova)
//   top_k          - top-k sampling parameter (plain integer)
//   top_p_bits     - top-p as int64 double bits (use to_float() in Nova)
//
// Returns: number of bytes written to output_buf, or -1 on error.
int nova_generate(struct llama_model* model, const char* prompt, int prompt_len,
                  char* output_buf, int output_buf_size, int max_tokens,
                  int64_t temp_bits, int top_k, int64_t top_p_bits) {
    if (!model || !prompt || !output_buf) return -1;

    float temp = bits_to_float(temp_bits);
    float top_p = bits_to_float(top_p_bits);

    // Create context with enough room for prompt + generation
    struct llama_context_params* ctx_params = nova_ctx_default_params();
    int ctx_size = max_tokens + 512;
    if (ctx_size < 2048) ctx_size = 2048;
    nova_ctx_params_set(ctx_params, ctx_size, 512, 4);
    struct llama_context* ctx = nova_create_context(model, ctx_params);
    if (!ctx) return -1;

    // Tokenize the prompt
    int token_buf_size = prompt_len + max_tokens + 512;
    int32_t* tokens = (int32_t*)malloc(sizeof(int32_t) * token_buf_size);
    if (!tokens) {
        nova_free_context(ctx);
        return -1;
    }
    int n_tokens = llama_tokenize(model, prompt, prompt_len, tokens, token_buf_size, true, false);
    if (n_tokens < 0) n_tokens = -n_tokens;
    if (n_tokens == 0) {
        free(tokens);
        nova_free_context(ctx);
        return -1;
    }

    // Create batch with prompt tokens
    struct llama_batch* batch = nova_batch_create(tokens, n_tokens);
    if (!batch) {
        free(tokens);
        nova_free_context(ctx);
        return -1;
    }

    // Decode prompt (forward pass)
    if (nova_decode(ctx, batch) != 0) {
        nova_batch_free(batch);
        free(tokens);
        nova_free_context(ctx);
        return -1;
    }

    // Create sampler
    struct llama_sampler* smpl = llama_sampler_chain_init(
        llama_sampler_chain_default_params());
    if (temp <= 0.0f) {
        llama_sampler_chain_add(smpl, llama_sampler_init_greedy());
    } else {
        llama_sampler_chain_add(smpl, llama_sampler_init_top_k(top_k));
        llama_sampler_chain_add(smpl, llama_sampler_init_top_p(top_p, 1));
        llama_sampler_chain_add(smpl, llama_sampler_init_temp(temp));
        llama_sampler_chain_add(smpl, llama_sampler_init_dist(0));
    }

    int output_pos = 0;
    int cur_pos = n_tokens;

    for (int i = 0; i < max_tokens; i++) {
        // Sample next token
        int32_t new_token = llama_sampler_sample(smpl, ctx, -1);

        // Check for end-of-generation
        if (llama_token_is_eog(model, new_token)) break;

        // Convert token to text piece
        char piece[256];
        int piece_len = llama_token_to_piece(model, new_token, piece, sizeof(piece), 0, true);
        if (piece_len > 0 && output_pos + piece_len < output_buf_size) {
            memcpy(output_buf + output_pos, piece, piece_len);
            output_pos += piece_len;
        } else if (output_pos + piece_len >= output_buf_size) {
            break;  // output buffer full
        }

        // Prepare batch for next token
        nova_batch_clear(batch);
        nova_batch_add_token(batch, new_token, cur_pos);
        cur_pos++;

        // Decode (forward pass for the new token)
        if (nova_decode(ctx, batch) != 0) break;
    }

    if (output_pos < output_buf_size) {
        output_buf[output_pos] = '\0';
    } else {
        output_buf[output_buf_size - 1] = '\0';
    }

    llama_sampler_free(smpl);
    nova_batch_free(batch);
    free(tokens);
    nova_free_context(ctx);

    return output_pos;
}
