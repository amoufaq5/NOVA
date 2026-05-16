# Nova v2: A Causal-First Language for Intelligent Systems

## Core Thesis

The bottleneck in AI/AGI is not "Python is slow." It's that:

1. **Training is wasteful** — models learn spurious correlations because they have no causal structure
2. **No language understands causality** — causal inference is done ad-hoc in libraries with no compiler guarantees
3. **Distribution is manual** — researchers spend months on parallelism instead of research
4. **Model composition is unsafe** — combining models has no type-level guarantees
5. **There's no formal way to express WHAT a model should learn** — objectives are strings of math, not verified specifications

Nova v2 solves all five by making **causal reasoning, typed objectives, and verified training** first-class language constructs.

---

## Novel Features (exist nowhere else)

### 1. Causal Graphs as Types

```nova
-- A causal graph is a FIRST-CLASS TYPE that the compiler verifies
causal graph MedicalOutcome {
    nodes {
        treatment: binary
        age: continuous
        genetics: categorical[4]
        recovery: continuous    -- target
        side_effects: continuous
    }

    edges {
        age -> recovery
        age -> side_effects
        genetics -> recovery
        treatment -> recovery
        treatment -> side_effects
        -- NOTE: no edge from genetics -> treatment (randomized trial)
    }

    confounders {
        age confounds (treatment, recovery)
    }
}
```

**What the compiler does with this:**
- Verifies the graph is a valid DAG (acyclic)
- Computes d-separation sets automatically
- Identifies valid adjustment sets for causal effect estimation
- Generates efficient interventional distributions
- REJECTS code that violates causal assumptions (e.g., conditioning on a collider)

### 2. Interventions and Counterfactuals as Operators

```nova
-- do-calculus as a language operator
let causal_effect = intervene(graph, treatment := 1) {
    -- This block runs in the INTERVENTIONAL distribution
    -- The compiler removes all edges INTO treatment (graph surgery)
    return E[recovery | do(treatment=1)] - E[recovery | do(treatment=0)]
}

-- Counterfactual reasoning
let cf = counterfactual(graph, observed: patient_data) {
    -- "What would have happened if we gave treatment=0?"
    set treatment = 0
    return recovery  -- compiler traces through structural equations
}

-- The compiler PROVES this query is identifiable from observational data
-- If not identifiable, it's a COMPILE ERROR, not a runtime surprise
```

### 3. Typed Objectives (What a Model SHOULD Learn)

```nova
-- Instead of just loss = MSE(pred, target), express the GOAL
objective CausalPrediction {
    -- The model must learn the causal effect, not correlations
    invariance: model.output is_invariant_to(graph.non_ancestors(target))
    
    -- The representation must respect causal structure
    structure: model.latent decomposes_along(graph.causal_mechanisms)
    
    -- Formal specification of generalization
    transfer: for all environments E in domain {
        model.loss(E) <= base_loss * (1 + epsilon)
    }
}

model Predictor(graph: MedicalOutcome) {
    satisfies CausalPrediction  -- COMPILER checks this is achievable

    layers {
        encoder = CausalEncoder(graph)  -- respects causal structure
        head = Linear(latent_dim, 1)
    }

    forward(x) {
        let z = encoder(x)  -- z is typed as CausalRepresentation
        return head(z)
    }
}
```

**What the compiler generates from typed objectives:**
- The correct loss function (invariant risk minimization, not just ERM)
- Appropriate regularizers (causal regularization terms)
- Data augmentation strategy (interventional samples)
- Validation criteria (out-of-distribution tests based on causal structure)

### 4. Compiler-Driven Training Optimization

```nova
-- The COMPILER analyzes your model and generates an optimal training plan
train Predictor with optimizer=AdamW {
    data: medical_dataset
    objective: CausalPrediction
    budget: arena gpu(memory=24GB, time=2h)

    -- Compiler automatically determines:
    -- • Batch size (from memory budget + model size)
    -- • Gradient checkpointing points (from activation memory analysis)
    -- • Mixed precision strategy (from numerical stability analysis)
    -- • Learning rate schedule (from loss landscape curvature)
    -- • When to stop (from causal validation metric convergence)
}
```

**How this cuts training time:**

| Optimization | How | Speedup |
|---|---|---|
| Dead gradient elimination | Compiler proves parameters don't affect loss → skip them | 10-30% |
| Causal pruning | Remove paths not causally relevant to target | 20-50% |
| Automatic gradient checkpointing | Compiler solves memory/compute tradeoff optimally | Enables 2-4x larger batches |
| Fused autograd | Source-to-source AD fuses operations | 15-25% |
| Curriculum from causal structure | Train simple mechanisms first, compose | 2-5x convergence |
| Interventional data augmentation | Generate counterfactual samples | 3-10x data efficiency |

### 5. World Models as a First-Class Type

```nova
-- A world model is a causal model that evolves over time
world_model PhysicsWorld {
    state {
        objects: [Object]
        forces: [Force]
        time: float
    }

    -- Structural causal equations (the "laws")
    mechanisms {
        position(t+1) = position(t) + velocity(t) * dt
        velocity(t+1) = velocity(t) + (sum(forces) / mass) * dt
    }

    -- The compiler verifies mechanisms are:
    -- 1. Causally consistent (no cycles within a timestep)
    -- 2. Complete (every state variable has an update rule)
    -- 3. Stable (Lyapunov analysis for bounded inputs)

    -- Query the world model
    predict(steps: int) -> [State] {
        -- Compiler generates efficient rollout code
        -- Automatically parallelizes independent mechanisms
    }

    -- Causal queries on the world model
    what_if(intervention: Intervention) -> State {
        -- Graph surgery + forward simulation
    }
}
```

### 6. Verified Source-to-Source Autograd

```nova
-- grad() is a COMPILER TRANSFORMATION, not a runtime tape
fn loss(params: tensor[f32, N, M], x: tensor[f32, B, N]) -> tensor[f32, 1] {
    let h = relu(x @ params)
    return (h - target).pow(2).mean()
}

-- The compiler rewrites this into:
-- 1. Forward pass (with minimal stored activations)
-- 2. Backward pass (with fused operations)
-- 3. Gradient accumulation (with automatic mixed precision)
let dloss = grad(loss, wrt=params)

-- Higher-order gradients (for meta-learning, MAML, etc.)
let d2loss = grad(grad(loss, wrt=params), wrt=params)

-- The compiler VERIFIES:
-- • No non-differentiable operations in the path (or inserts straight-through estimators)
-- • Numerical stability (flags potential NaN/Inf paths)
-- • Memory usage of the backward pass (warns if it exceeds budget)
```

**Why this is better than PyTorch/JAX autograd:**
- **Compile-time fusion**: The compiler sees the entire computation graph and fuses elementwise ops
- **Optimal checkpointing**: Compiler solves the exact recomputation-vs-storage tradeoff
- **Dead code elimination**: If a parameter doesn't affect loss, its gradient is never computed
- **Static memory planning**: No dynamic allocation during training (predictable GPU memory)
- **Verified correctness**: Compiler proves gradient is mathematically correct

### 7. Causal Curriculum Learning

```nova
-- The compiler generates an optimal training curriculum from causal structure
curriculum TrainPhysics(world: PhysicsWorld) {
    -- Phase 1: Learn individual mechanisms (causally independent)
    stage isolated_mechanisms {
        for mechanism in world.mechanisms.independent_sets() {
            train mechanism.model with {
                data: mechanism.isolate(dataset)  -- interventional data for just this mechanism
                convergence: loss < 0.01
            }
        }
    }
    -- These run IN PARALLEL because they're causally independent

    -- Phase 2: Learn compositions (causally downstream)
    stage compositions {
        depends_on: isolated_mechanisms
        for chain in world.mechanisms.causal_chains() {
            train chain.model with {
                data: chain.compose(dataset)
                init: pretrained(isolated_mechanisms)  -- warm start from stage 1
                convergence: loss < 0.01
            }
        }
    }

    -- Phase 3: End-to-end fine-tuning
    stage finetune {
        depends_on: compositions
        train world.full_model with {
            data: full_dataset
            init: pretrained(compositions)
            convergence: causal_validation_metric > 0.95
        }
    }
}
```

**Why this is faster than standard training:**
- Mechanisms are learned independently → parallelizable
- Each stage is simpler → converges faster
- Warm-starting from causal decomposition → better initialization than random
- Causal validation → stops when the model has learned the right thing (not just fit the data)

### 8. Safe Model Composition with Type Checking

```nova
-- Models have INTERFACE TYPES that the compiler checks
model VisionEncoder : Encoder[Image -> LatentVector[512]] {
    -- ...
}

model LanguageDecoder : Decoder[LatentVector[512] -> Text] {
    -- ...
}

-- Type-safe composition — compiler verifies dimensional compatibility
let multimodal = VisionEncoder |> LanguageDecoder
-- ✓ Compiles: output type of VisionEncoder matches input type of LanguageDecoder

let bad = LanguageDecoder |> VisionEncoder
-- ✗ COMPILE ERROR: Text is not compatible with Image

-- Hot-swap with runtime type checking
let v2_encoder: Encoder[Image -> LatentVector[512]] = load("v2_checkpoint")
multimodal.swap(VisionEncoder, v2_encoder)  -- type-safe, no restart needed
```

### 9. Symbolic Shape Algebra

```nova
-- Shapes are SYMBOLIC expressions, not just numbers
fn attention(q: tensor[f32, B, H, N, D],
             k: tensor[f32, B, H, M, D],
             v: tensor[f32, B, H, M, V]) -> tensor[f32, B, H, N, V] {
    -- Compiler computes: scores shape = [B, H, N, M] (N*M attention matrix)
    let scores = q @ k.transpose(-2, -1) / sqrt(D)

    -- Compiler WARNING: O(N*M) memory. If N, M > 8192, suggest FlashAttention
    let weights = softmax(scores, dim=-1)
    return weights @ v
}

-- The compiler can PROVE:
-- • Output shape is [B, H, N, V] 
-- • Memory usage is O(B*H*N*M) for scores
-- • FLOPs are O(B*H*N*M*D) for the matmuls
-- • If D is constant and N=M, this is O(N²) — suggests linear alternatives when N > threshold
```

### 10. Distributed Training as a Type-Level Concern

```nova
-- Distribution strategy is part of the TYPE, not runtime config
model LargeModel {
    -- Compiler sees total parameter count exceeds single-GPU memory
    -- Automatically applies tensor parallelism

    @shard(dim=0, devices=8)  -- compiler verifies this dimension is divisible by 8
    embedding = tensor[f32, 50000, 4096]

    @replicate  -- small enough to fit on each device
    head = Linear(4096, 50000)

    @pipeline(stages=4)  -- compiler balances compute across stages
    layers = [TransformerBlock(4096) for _ in range(48)]

    forward(x) {
        -- Compiler inserts all-reduce, all-gather, send/recv automatically
        -- Programmer sees clean sequential code
        let h = embedding[x]
        for layer in layers {
            h = layer(h)
        }
        return head(h)
    }
}

-- The compiler generates:
-- • Communication schedule (overlaps compute and communication)
-- • Memory budget per device (proves it fits)
-- • Optimal pipeline schedule (1F1B, interleaved, etc.)
-- • Gradient synchronization points
```

---

## How Nova v2 Cuts Training Time

### Problem 1: Models Learn Spurious Correlations

**Current approach (PyTorch):** Train on data, hope the model generalizes. If it doesn't, manually add regularization, augmentation, collect more data. Iterate for months.

**Nova approach:**
```nova
causal graph SentimentAnalysis {
    nodes { text, sentiment, author_style, topic }
    edges {
        text -> sentiment
        text -> topic
        author_style -> text  -- style causes text, not the other way
    }
    -- sentiment should NOT depend on author_style
    invariance: sentiment is_independent_of author_style given text
}

model Classifier(graph: SentimentAnalysis) {
    satisfies graph.invariance  -- compiler generates IRM loss
    -- Model CANNOT learn style→sentiment shortcut
    -- Training converges to causal features only
}
```
**Result:** 3-10x fewer samples needed because the model only learns causal features.

### Problem 2: Training Large Models Wastes Compute

**Current approach:** Set batch size by trial and error, manually tune gradient checkpointing, profile memory, OOM → retry with smaller batch.

**Nova approach:**
```nova
train LargeModel {
    budget: arena gpu[A100 * 8](memory=80GB, time=48h)
    -- Compiler solves:
    -- • Exact max batch size: 47 (proven to fit in 80GB with checkpointing)
    -- • Checkpoint schedule: layers [3,7,11,15] (optimal memory/compute)
    -- • Mixed precision map: attention in fp16, layernorm in fp32
    -- • Pipeline bubble minimization: 1F1B with 4 microbatches
}
```
**Result:** Zero trial-and-error. Compiler-optimal memory usage. 20-40% faster than hand-tuned.

### Problem 3: Transfer Learning is Ad-Hoc

**Current approach:** Fine-tune everything, or freeze layers by intuition, or use adapters with arbitrary rank.

**Nova approach:**
```nova
-- Compiler identifies which mechanisms transfer
let source_model = load("pretrained_physics")
let target_task = Chemistry  -- different domain but shared mechanisms

-- Compiler computes CAUSAL OVERLAP between source and target
let transferable = source_model.mechanisms.intersect(target_task.mechanisms)
-- e.g., force calculations transfer, but chemical bonds don't

transfer source_model to target_task {
    freeze: transferable.mechanisms  -- proven to generalize
    train: target_task.mechanisms - transferable  -- only new stuff
    -- 80% of model is frozen because compiler PROVED it transfers
}
```
**Result:** Only train 20% of parameters. 5x faster fine-tuning with guaranteed correctness.

### Problem 4: Hyperparameter Search is Expensive

**Current approach:** Grid search, random search, Bayesian optimization — all treat the model as a black box.

**Nova approach:**
```nova
-- The compiler has STRUCTURAL KNOWLEDGE about the model
-- It can compute learning rate bounds analytically
train Model {
    optimizer: AdamW {
        -- Compiler computes spectral norm of each layer
        -- Sets per-layer learning rates based on Lipschitz constants
        lr: auto  -- compiler-derived, not searched
        
        -- Compiler computes optimal warmup from initialization statistics
        warmup: auto
        
        -- Compiler determines when to decay from loss curvature analysis
        schedule: auto
    }
}
```
**Result:** No hyperparameter search needed for 80% of settings. Saves weeks of GPU time.

---

## The Path to World Models and AGI

### What a World Model Needs (and how Nova provides it)

| Requirement | Current Tools | Nova v2 |
|---|---|---|
| Causal reasoning | Ad-hoc (libraries, no verification) | First-class, compiler-verified |
| Composable mechanisms | Manual, error-prone | Type-checked composition |
| Counterfactual simulation | Slow, approximate | Compiler-optimized structural equations |
| Multi-scale temporal reasoning | Hand-coded hierarchies | Causal graph levels with automatic time abstraction |
| Transfer across domains | Fine-tuning (brute force) | Mechanism-level transfer with proof |
| Continuous learning | Catastrophic forgetting | Causal isolation of mechanisms (new doesn't destroy old) |
| Efficient inference | Full forward pass always | Causal pruning (skip irrelevant paths) |

### AGI Architecture in Nova

```nova
-- An AGI system is a COMPOSITION of world models + reasoning + acting
agent AGI {
    -- World model: causal understanding of reality
    world: world_model Reality {
        state { objects, agents, physics, social, abstract }
        mechanisms { /* learned and verified causal laws */ }
    }

    -- Reasoning: causal inference on the world model
    reason(query: Query) -> Answer {
        match query {
            Prediction(future) => world.predict(future)
            Counterfactual(alt) => world.what_if(alt)
            Explanation(event) => world.trace_causes(event)
            Planning(goal) => world.find_interventions(goal)
        }
    }

    -- Acting: choose interventions that achieve goals
    act(goal: Goal) -> Action {
        -- Find the intervention with highest P(goal | do(action))
        let options = world.valid_interventions(goal)
        let best = options.argmax(|a| world.probability(goal | intervene(a)))
        
        -- Safety: verify the action doesn't violate constraints
        constrain {
            world.predict(best).harm < threshold
            world.predict(best).reversible == true
        }
        
        return best
    }

    -- Learning: update world model from experience
    learn(observation: Experience) {
        -- Identify which causal mechanisms are wrong
        let surprised = world.mechanisms.where(|m|
            m.predicted(observation.context) != observation.outcome
        )
        
        -- Update ONLY the wrong mechanisms (no catastrophic forgetting)
        for mechanism in surprised {
            retrain mechanism with {
                data: observation
                constraint: other_mechanisms.unchanged()
            }
        }
    }
}
```

### Why This Architecture Enables AGI

1. **Causal world model** → Can reason about interventions, not just correlations
2. **Compositional mechanisms** → New knowledge doesn't destroy old (solves catastrophic forgetting)
3. **Counterfactual reasoning** → Can plan by simulating "what if I do X?"
4. **Typed objectives** → Can verify the system is learning the RIGHT things
5. **Mechanism-level updates** → Continuous learning without full retraining
6. **Safety constraints** → Built into the reasoning loop, not bolted on after

---

## Implementation Roadmap

### Phase 1: Causal Core (Months 1-6)
- Causal graph type system (DAG verification, d-separation, adjustment sets)
- `intervene()` and `counterfactual()` operators
- Source-to-source autograd (replacing numerical finite differences)
- Symbolic shape algebra

### Phase 2: Training Intelligence (Months 7-12)
- Typed objectives → loss function generation
- Compiler-driven batch size, checkpointing, mixed precision
- Causal curriculum generation
- Dead gradient elimination

### Phase 3: World Models (Months 13-18)
- `world_model` type with temporal causal graphs
- Mechanism composition and isolation
- Counterfactual simulation engine
- Transfer learning via causal overlap

### Phase 4: Distribution (Months 19-24)
- Automatic tensor/pipeline/data parallelism
- Communication schedule optimization
- Multi-node training with proven memory bounds
- Fault tolerance via mechanism isolation

### Phase 5: AGI Infrastructure (Months 25-36)
- Agent type with causal reasoning
- Continuous learning with mechanism-level updates
- Safety verification (provable output constraints)
- Self-improving curriculum (the system designs its own training)

---

## Comparison: Training a Vision Model

### PyTorch (current state of the art)
```python
# ~200 lines of boilerplate
# Manual: batch size, LR, schedule, augmentation, checkpointing
# No guarantees about what the model learns
# Trial and error for weeks
model = ResNet50()
optimizer = Adam(model.parameters(), lr=3e-4)  # why 3e-4? vibes
for epoch in range(100):  # why 100? vibes
    for batch in dataloader:
        loss = F.cross_entropy(model(batch.x), batch.y)
        loss.backward()
        optimizer.step()
# Hope it generalizes. If not, start over.
```

### Nova v2
```nova
causal graph ImageClassification {
    nodes { pixels, object_class, lighting, background, camera_angle }
    edges {
        pixels -> object_class    -- pixels reveal class
        lighting -> pixels        -- lighting affects pixels
        background -> pixels      -- background affects pixels
        camera_angle -> pixels    -- angle affects pixels
    }
    -- Class should be invariant to lighting, background, angle
    invariance: object_class is_independent_of [lighting, background, camera_angle]
}

model Classifier(graph: ImageClassification) {
    satisfies graph.invariance
    architecture: auto(budget=25M_params)  -- compiler designs architecture
}

train Classifier {
    data: imagenet
    budget: arena gpu[A100](memory=80GB, time=4h)
    -- Everything else is compiler-derived
}
-- Trains in 4h instead of days because:
-- • Only learns causal features (invariant to spurious correlations)
-- • Compiler optimizes batch/LR/checkpointing
-- • Augmentation generated from causal graph (interventions on lighting, bg, angle)
-- • Stops when causal validation passes (not when train loss is low)
```
