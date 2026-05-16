# Nova — A Cognitive Architecture Language

Nova is a language for building thinking machines. Not LLMs. Not chatbots.
Machines that remember, reason, learn from experience, imagine scenarios,
and take initiative.

## Core Idea

Nova code IS the AI brain. When you write Nova, you're wiring up a mind:
- **Memory** — short-term (working) + long-term (episodic, semantic, procedural)
- **Perception** — multimodal input processing (text, image, audio, data)
- **Reasoning** — logic, causality, analogy, consequence prediction
- **Learning** — reinforcement + bayesian belief updating + pattern extraction
- **Imagination** — world model simulation + memory recombination
- **Initiative** — internal goal generation, not just responding to prompts

## Architecture

```
Perception → Working Memory → Reasoning → Action
                  ↑↓                ↑↓
           Long-Term Memory    Imagination
                  ↑↓                ↑↓
              Learning         World Model
```

## Quick Example

```nova
mind Agent {
    -- Memory systems
    memory working(capacity: 7)
    memory episodic(max_episodes: 100000)
    memory semantic(structure: graph)
    memory procedural(structure: rules)

    -- Perception
    perceive(input) {
        let parsed = recognize(input)
        working.hold(parsed)
        if parsed.importance > 0.7 {
            episodic.store(parsed, context: working.snapshot())
        }
    }

    -- Reasoning
    think(goal) {
        let relevant = semantic.recall(goal, limit: 20)
        let past = episodic.similar(goal, limit: 5)
        let plan = reason(goal, knowledge: relevant, experience: past)
        return plan
    }

    -- Imagination
    imagine(scenario) {
        let sim = world_model.simulate(scenario, steps: 10)
        let consequences = sim.outcomes()
        return consequences
    }

    -- Learning
    on experience(event) {
        -- Reinforcement: was the outcome good or bad?
        let reward = evaluate(event.outcome, event.goal)
        procedural.update(event.action, reward)

        -- Bayesian: update beliefs
        semantic.update_belief(event.observation)

        -- Pattern: extract rules from repeated experiences
        let patterns = episodic.find_patterns(event, min_occurrences: 3)
        for pattern in patterns {
            semantic.store_rule(pattern)
        }
    }

    -- Initiative
    on idle() {
        let goals = assess_situation(working.contents(), semantic)
        if goals.any_urgent() {
            act(goals.most_urgent())
        } else {
            -- Imagination during idle: "what could happen?"
            let scenarios = imagine(working.recent_context())
            for scenario in scenarios {
                if scenario.risk > 0.5 {
                    prepare(scenario)
                }
            }
        }
    }
}
```

## Running

```bash
nova run mind.nova          # Start the mind
nova interact mind.nova     # Interactive conversation
nova test mind.nova         # Run cognitive tests
```

## Phase 0 — Bootstrap Interpreter in Python
Single-agent, local execution. Python is the host runtime.
Prove the cognitive architecture works before optimizing.
