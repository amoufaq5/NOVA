# Nova Standard Library Reference — N12–N29

Cognitive, runtime, and tooling modules added in v3.0. All modules are
available via `import "std/<name>"`. Values use scale-1000 fixed-point
(500 = 0.5, 1000 = 1.0) unless noted otherwise.

---

## N12 — Associative Memory (`import "std/associative"`)

Hopfield-network content-addressable memory. Stores patterns and recalls
the nearest match from partial or noisy input.

| Function | Description |
|---|---|
| `associative_new(capacity, dims)` | Create network with given capacity and vector dimensions |
| `associative_store(net, pattern)` | Store a pattern (list of ints) |
| `associative_recall(net, partial)` | Recall closest stored pattern |
| `associative_energy(net, pattern)` | Compute Hopfield energy for a pattern |
| `associative_nearest(net, query)` | Find nearest stored pattern index |
| `associative_capacity_used(net)` | Number of stored patterns |
| `associative_clear(net)` | Remove all stored patterns |

---

## N13 — Hyperdimensional Computing (`import "std/hdc"`)

Bipolar hyperdimensional vectors for symbolic AI. Operations preserve
dimensionality and enable compositional representations.

| Function | Description |
|---|---|
| `hdc_new(dims, seed)` | Create HDC context with dimension count and PRNG seed |
| `hdc_random_vector(hdc)` | Generate random bipolar vector |
| `hdc_bind(a, b)` | Element-wise XOR binding |
| `hdc_unbind(a, b)` | Inverse binding (same as bind for bipolar) |
| `hdc_bundle(vectors)` | Majority-vote bundling of a list of vectors |
| `hdc_similarity(a, b)` | Cosine similarity (scale-1000) |
| `hdc_permute(v)` | Circular shift by 1 position |
| `hdc_dimensions(hdc)` | Get dimension count |

---

## N14 — Sparse Distributed Representations (`import "std/sdr"`)

SDRs using sorted active-index lists. Supports encoding, overlap
computation, and set operations.

| Function | Description |
|---|---|
| `sdr_new(dims, sparsity)` | Create SDR with given total dimensions and target sparsity |
| `sdr_set(sdr, indices)` | Set active indices (sorted list) |
| `sdr_get_active(sdr)` | Get list of active indices |
| `sdr_active_count(sdr)` | Number of active bits |
| `sdr_overlap(a, b)` | Count of shared active indices |
| `sdr_union(a, b)` | Union of active indices |
| `sdr_intersection(a, b)` | Intersection of active indices |
| `sdr_subsample(sdr, fraction)` | Randomly subsample active indices |
| `sdr_random(sdr, count)` | Activate `count` random indices |
| `sdr_similarity(a, b)` | Overlap ratio (scale-1000) |
| `sdr_encode_scalar(sdr, val, min, max)` | Encode scalar as SDR |
| `sdr_describe(sdr)` | Print description |

---

## N15 — Active Inference (`import "std/active_inference"`)

Free-energy minimization using Beta distribution priors. Models
belief updating and action selection under uncertainty.

| Function | Description |
|---|---|
| `ai_new(num_states)` | Create model with given state count |
| `ai_set_prior(model, state, alpha, beta)` | Set Beta prior for a state |
| `ai_set_likelihood(model, state, obs, prob)` | Set observation likelihood |
| `ai_observe(model, observation)` | Record an observation |
| `ai_predict(model)` | Return predicted state (highest posterior) |
| `ai_free_energy(model)` | Compute variational free energy |
| `ai_select_action(model, num_actions)` | Select action minimizing expected free energy |
| `ai_posterior(model)` | Get posterior distribution |
| `ai_reset(model)` | Reset to priors |
| `ai_state_count(model)` | Number of states |
| `ai_describe(model)` | Print model summary |

---

## N16 — Time Series (`import "std/timeseries"`)

Windowed aggregation, decay functions, temporal joins, and resampling.
Timestamps and values are integers.

| Function | Description |
|---|---|
| `ts_new(capacity)` | Create time series with max point count |
| `ts_append(ts, timestamp, value)` | Add a data point (maintains sorted order) |
| `ts_count(ts)` | Number of stored points |
| `ts_get(ts, index)` | Get [timestamp, value] pair at index |
| `ts_window(ts, start, end)` | Get points in time range |
| `ts_sum(ts, start, end)` | Sum of values in range |
| `ts_mean(ts, start, end)` | Mean of values in range |
| `ts_max(ts, start, end)` | Maximum value in range |
| `ts_min(ts, start, end)` | Minimum value in range |
| `ts_count_window(ts, start, end)` | Count of points in range |
| `ts_variance(ts, start, end)` | Variance of values in range (scale-1000) |
| `ts_decay_exponential(ts, half_life, now)` | Apply exponential decay |
| `ts_decay_power_law(ts, exponent, now)` | Apply power-law decay |
| `ts_join(a, b, tolerance)` | Temporal join within tolerance |
| `ts_resample(ts, interval)` | Resample with linear interpolation |
| `ts_describe(ts)` | Print summary |

---

## N17 — Audio Processing (`import "std/audio"`)

STFT, MFCC, voice activity detection. Sample values are integers.
Includes Taylor series trig and Newton's method sqrt.

| Function | Description |
|---|---|
| `audio_new(sample_rate)` | Create empty audio buffer |
| `audio_from_samples(samples, rate)` | Create from sample list |
| `audio_sample_count(a)` | Number of samples |
| `audio_get_sample(a, i)` | Get sample at index |
| `audio_set_sample(a, i, val)` | Set sample at index |
| `audio_append_samples(a, samples)` | Append sample list |
| `audio_stft(a, frame_size, hop)` | Short-time Fourier transform |
| `audio_magnitude_spectrum(frame)` | Magnitude spectrum of STFT frame |
| `audio_mfcc(a, num_coeffs, frame_size)` | Mel-frequency cepstral coefficients |
| `audio_voice_activity(a, frame_size, threshold)` | Voice activity detection |
| `audio_normalize(a)` | Normalize to [-1000, 1000] |
| `audio_energy(a)` | Total energy of signal |

---

## N18 — Node Pool (`import "std/node_pool"`)

Pre-allocated pool with O(1) claim/release via free list.
Each slot holds `node_size` fields.

| Function | Description |
|---|---|
| `pool_new(capacity, node_size)` | Create pool |
| `pool_claim(pool)` | Claim a free slot (returns index) |
| `pool_release(pool, slot)` | Release slot back to pool |
| `pool_get(pool, slot, field)` | Get field value from slot |
| `pool_set(pool, slot, field, val)` | Set field value in slot |
| `pool_get_values(pool, slot)` | Get all field values for slot |
| `pool_capacity(pool)` | Total capacity |
| `pool_used(pool)` | Used slot count |
| `pool_available(pool)` | Free slot count |
| `pool_is_used(pool, slot)` | Check if slot is in use |
| `pool_clear(pool)` | Release all slots |
| `pool_describe(pool)` | Print summary |

---

## N19 — Resonance Kernel (`import "std/resonance"`)

Similarity-based pattern matching with preference vectors and
configurable activation functions.

| Function | Description |
|---|---|
| `resonance_new(type_count, pattern_dim)` | Create kernel |
| `resonance_set_preference(k, type, pattern)` | Set preference vector for signal type |
| `resonance_set_weight(k, type, weight)` | Set weight for signal type |
| `resonance_set_activation(k, fn_id)` | Set activation: 0=identity, 1=relu |
| `resonance_compute(k, signals)` | Compute activation from signal list |
| `resonance_similarity(k, type, pattern)` | Cosine similarity with preference |
| `resonance_update_preference(k, type, observed, rate)` | Move preference toward observed |
| `resonance_reset(k)` | Clear preferences and history |
| `resonance_history(k)` | Get activation history |
| `resonance_describe(k)` | Print summary |

---

## N20 — Atom Lifecycle (`import "std/atom_lifecycle"`)

Birth/death mechanism for atoms based on co-activation recurrence
and activity decay.

### Birth Monitor

| Function | Description |
|---|---|
| `birth_monitor_new(threshold)` | Create monitor (birth after `threshold` observations) |
| `birth_observe(mon, pattern, timestamp)` | Record co-activation pattern |
| `birth_pending_births(mon)` | Get patterns ready for promotion |
| `birth_promote(mon, hash)` | Promote pattern to atom, returns atom ID |
| `birth_pattern_count(mon)` | Number of tracked patterns |
| `birth_clear(mon)` | Clear all patterns |

### Death Monitor

| Function | Description |
|---|---|
| `death_monitor_new(decay, threshold, ticks)` | Create monitor |
| `death_register_atom(mon, atom_id)` | Register atom (initial activity 1000) |
| `death_observe(mon, atom_id, activity)` | Update atom activity via EMA |
| `death_tick(mon)` | Decay all atoms by decay rate |
| `death_pending_deaths(mon)` | Get atoms below threshold for sustained ticks |
| `death_release(mon, atom_id)` | Remove atom from monitor |
| `death_atom_activity(mon, atom_id)` | Get current activity level |
| `death_atom_count(mon)` | Number of monitored atoms |
| `death_describe(mon)` | Print all atom states |

---

## N21 — Visualizer (`import "std/visualizer"`)

Signal-flow logging with HTML/SVG export. Tracks nodes, synapses,
and signal events.

| Function | Description |
|---|---|
| `viz_new()` | Create visualizer |
| `viz_register_node(v, id, label, x, y)` | Add node with position |
| `viz_register_synapse(v, from, to)` | Add directed edge |
| `viz_log_signal(v, from, to, mag, time)` | Log signal event |
| `viz_log_activation(v, node, mag, time)` | Log activation event |
| `viz_event_count(v)` | Total logged events |
| `viz_node_count(v)` | Number of nodes |
| `viz_synapse_count(v)` | Number of synapses |
| `viz_events_for_node(v, node_id)` | Get events involving a node |
| `viz_hottest_nodes(v, count)` | Most active nodes |
| `viz_flow_between(v, from, to)` | Total flow between two nodes |
| `viz_export_html(v, title)` | Export as HTML with embedded SVG |
| `viz_stop(v)` | Stop recording |
| `viz_clear_events(v)` | Clear event log |
| `viz_describe(v)` | Print summary |

---

## N22 — Time Machine (`import "std/time_machine"`)

Snapshot-based debugger with rewind, replay, and trajectory comparison.

| Function | Description |
|---|---|
| `tm_new(capacity)` | Create time machine with snapshot capacity |
| `tm_record_tick(tm, tick_id, snapshot)` | Save snapshot at tick |
| `tm_tick_count(tm)` | Number of recorded ticks |
| `tm_rewind_to(tm, tick_id)` | Get snapshot at tick |
| `tm_get_tick_range(tm, start, end)` | Get snapshots in range |
| `tm_replay_from(tm, tick_id)` | Get all snapshots from tick onward |
| `tm_save_trajectory(tm, name)` | Save current tick sequence as named trajectory |
| `tm_get_trajectory(tm, name)` | Retrieve saved trajectory |
| `tm_compare_trajectories(tm, n1, n2)` | Compare two trajectories |
| `tm_snapshot_at(tm, tick_id)` | Get specific snapshot |
| `tm_pause(tm)` / `tm_resume(tm)` | Pause/resume recording |
| `tm_clear(tm)` | Clear all snapshots |
| `tm_describe(tm)` | Print summary |

---

## N23 — KG Visualizer (`import "std/kg_visualizer"`)

Knowledge graph browser with BFS subgraph extraction and export to
HTML/SVG and DOT format. Requires `import "std/knowledge"`.

| Function | Description |
|---|---|
| `kgviz_new(kg)` | Create visualizer for a knowledge graph |
| `kgviz_set_depth(v, depth)` | Set BFS traversal depth |
| `kgviz_add_filter(v, type)` | Add entity type filter |
| `kgviz_clear_filters(v)` | Remove all filters |
| `kgviz_render_subgraph(v, entity)` | BFS subgraph from entity |
| `kgviz_search(v, query)` | Search entities by substring |
| `kgviz_entity_info(v, entity)` | Get entity details |
| `kgviz_stats(v)` | Get graph statistics |
| `kgviz_export_html(v, title)` | Export subgraph as HTML/SVG |
| `kgviz_export_dot(v, title)` | Export as Graphviz DOT |
| `kgviz_describe(v)` | Print summary |

---

## N24 — Profiler (`import "std/profiler"`)

Per-section timing and event counting with flamegraph export.
Timing uses tick counts (call `prof_tick` each cycle).

| Function | Description |
|---|---|
| `prof_new()` | Create profiler |
| `prof_tick(p)` | Advance tick counter |
| `prof_set_tick(p, tick)` | Set tick counter directly |
| `prof_start_section(p, name)` | Begin timing a named section |
| `prof_end_section(p, name)` | End timing a named section |
| `prof_section_total(p, name)` | Total ticks spent in section |
| `prof_section_count(p, name)` | Number of times section was entered |
| `prof_section_average(p, name)` | Average ticks per section invocation |
| `prof_count_event(p, cat, type)` | Increment event counter |
| `prof_count_event_n(p, cat, type, n)` | Add `n` to event counter |
| `prof_get_count(p, cat, type)` | Get event count |
| `prof_report(p)` | Print timing report |
| `prof_export_flamegraph(p)` | Export as flamegraph text |
| `prof_reset(p)` | Reset all counters |
| `prof_stop(p)` / `prof_start(p)` | Pause/resume profiling |
| `prof_describe(p)` | Print summary |

---

## N25 — Causal Library (`import "std/causal_library"`)

1000+ causal patterns across 6 categories, generated from 50 seed
patterns with 19 modifier variations each.

Categories: physical, biological, social, computational, causal, domain.

| Function | Description |
|---|---|
| `causal_lib_new()` | Create empty library |
| `causal_lib_load(lib)` | Load all 1000+ patterns |
| `causal_lib_count(lib)` | Total pattern count |
| `causal_lib_get(lib, id)` | Get pattern by ID |
| `causal_lib_search(lib, keyword)` | Search by keyword, returns matching IDs |
| `causal_lib_search_category(lib, cat)` | Search by category |
| `causal_lib_apply(lib, id, entity, ctx)` | Apply pattern, returns [effect, strength, applicable] |
| `causal_lib_categories(lib)` | List all categories |
| `causal_lib_describe(lib)` | Print summary with per-category counts |

Pattern structure: `[TAG, id, category, precondition, action, effect, strength, keywords]`

---

## N26 — Predictive Coding (`import "std/predictive_coding"`)

Top-down prediction with bottom-up error propagation. Supports
single layers and stacked hierarchies.

### Layer

| Function | Description |
|---|---|
| `pc_layer_new(in_dim, out_dim)` | Create layer |
| `pc_predict(layer, input)` | Generate prediction from input |
| `pc_compute_error(layer, pred, actual)` | Compute prediction error |
| `pc_update_weights(layer, err, input, lr)` | Update weights to reduce error |
| `pc_layer_input_dim(layer)` | Input dimension |
| `pc_layer_output_dim(layer)` | Output dimension |

### Stack

| Function | Description |
|---|---|
| `pc_stack_new(dims)` | Create stack from dimension list (N dims → N-1 layers) |
| `pc_stack_run(stack, obs)` | Forward pass, returns per-layer predictions |
| `pc_stack_learn(stack, obs, lr)` | Learn from observation, returns total error |
| `pc_stack_imagine(stack, top)` | Generate observation from top-level prediction |
| `pc_stack_layer_count(stack)` | Number of layers |
| `pc_stack_get_layer(stack, i)` | Get layer at index |

---

## N27 — Skill (`import "std/skill"`)

Procedural rules with triggers, actions, preconditions, and
success/failure tracking.

| Function | Description |
|---|---|
| `skill_new(name, description)` | Create skill |
| `skill_add_trigger(s, trigger)` | Add trigger condition |
| `skill_add_action(s, action)` | Add action step |
| `skill_add_precondition(s, cond)` | Add precondition |
| `skill_name(s)` / `skill_description(s)` | Get name/description |
| `skill_triggers(s)` / `skill_actions(s)` / `skill_preconditions(s)` | Get lists |
| `skill_record_outcome(s, success)` | Record attempt (1=success, 0=failure) |
| `skill_success_rate(s)` | Success rate (scale-1000) |
| `skill_attempt_count(s)` | Total attempts |
| `skill_matches_trigger(s, input)` | Check if input matches any trigger |
| `skill_check_preconditions(s, context)` | Check if all preconditions met |
| `skill_enable(s)` / `skill_disable(s)` | Enable/disable |
| `skill_is_active(s)` | Check if active |
| `skill_reset_stats(s)` | Reset attempt counters |
| `skill_describe(s)` | Print summary |

---

## N28 — Self-Model (`import "std/self_model"`)

Introspection API with emotion/focus/confidence tracking, goal
management, and natural language self-description. Reads OCEAN
personality values from a soul handle.

| Function | Description |
|---|---|
| `sm_new(soul_handle, history_cap)` | Create self-model (pass 0 for no soul) |
| `sm_update_state(sm, emotion, focus, confidence)` | Update current state |
| `sm_add_goal(sm, goal)` | Add active goal |
| `sm_remove_goal(sm, goal)` | Remove goal |
| `sm_update_goals(sm, goals_list)` | Replace all goals |
| `sm_current_emotion(sm)` | Get current emotion string |
| `sm_current_focus(sm)` | Get current focus string |
| `sm_current_confidence(sm)` | Get confidence (0–1000) |
| `sm_active_goals(sm)` | Get goals list |
| `sm_goal_count(sm)` | Number of active goals |
| `sm_set_soul(sm, soul_handle)` | Attach soul handle |
| `sm_describe(sm)` | Structured description as list |
| `sm_describe_natural_language(sm)` | Natural language self-description |
| `sm_history(sm, count)` | Recent state history entries |
| `sm_emotion_trend(sm, count)` | Most frequent recent emotion |
| `sm_confidence_trend(sm, count)` | Average recent confidence |

---

## N29 — Federation (`import "std/federation"`)

Anonymous insight sharing between tenant instances. Local-only mode
simulates federation within one process.

### Node

| Function | Description |
|---|---|
| `fed_node_new(instance_id, peers)` | Create federation node |
| `fed_extract_insight(node, cat, data, weight)` | Create insight from raw data |
| `fed_publish_insight(node, insight)` | Add insight to outbox |
| `fed_receive_insight(node, insight)` | Add insight to inbox |
| `fed_subscribe(node, category)` | Subscribe to category |
| `fed_unsubscribe(node, category)` | Unsubscribe from category |
| `fed_validate_insight(node, insight)` | Validate insight, returns trust-weighted score |
| `fed_integrate_insight(node, insight, kg)` | Integrate into knowledge graph |
| `fed_set_trust(node, peer_id, trust)` | Set trust level for peer |
| `fed_get_trust(node, peer_id)` | Get trust level (default 500) |
| `fed_inbox_count(node)` | Inbox size |
| `fed_outbox_count(node)` | Outbox size |
| `fed_get_inbox(node, i)` / `fed_get_outbox(node, i)` | Get message at index |
| `fed_clear_inbox(node)` | Clear inbox |
| `fed_close(node)` | Deactivate node |
| `fed_describe(node)` | Print summary |

### Insight structure

`[INSIGHT_TAG, id, source_id, category, content, evidence_weight, timestamp, signature]`
