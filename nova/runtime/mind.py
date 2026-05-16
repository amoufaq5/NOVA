"""Nova Mind — the cognitive architecture that ties everything together.

A Mind is an autonomous cognitive agent with:
- Four memory systems (working, episodic, semantic, procedural)
- Reasoning engine (logic, causality, analogy)
- Imagination engine (simulation + recombination)
- Learning engine (reinforcement + bayesian + patterns)
- Initiative system (goal generation, proactive behavior)
- Perception (input processing)

This is the runtime representation — Nova code creates and configures a Mind.
"""

import time
from .memory import WorkingMemory, EpisodicMemory, SemanticMemory, ProceduralMemory
from .reasoning import ReasoningEngine
from .imagination import ImaginationEngine
from .learning import LearningEngine


class Mind:
    """A complete cognitive agent."""

    def __init__(self, name="agent", config=None):
        self.name = name
        self.config = config or {}
        self.created_at = time.time()

        wm_capacity = self.config.get("working_capacity", 7)
        ep_max = self.config.get("max_episodes", 100000)

        self.working = WorkingMemory(capacity=wm_capacity)
        self.episodic = EpisodicMemory(max_episodes=ep_max)
        self.semantic = SemanticMemory()
        self.procedural = ProceduralMemory()

        self.reasoning = ReasoningEngine(self.semantic, self.episodic)
        self.imagination = ImaginationEngine(self.semantic, self.episodic)
        self.learning = LearningEngine(
            self.working, self.episodic, self.semantic, self.procedural)

        self.goals: list[dict] = []
        self.drives: dict[str, float] = {
            "curiosity": 0.5,
            "self_preservation": 0.8,
            "goal_achievement": 0.6,
            "social": 0.4,
        }

        self.event_handlers: dict[str, list] = {}
        self.reflexes: list[dict] = []
        self.perceive_fn = None
        self.think_fn = None
        self.imagine_fn = None
        self.learn_fn = None
        self.act_fn = None
        self.idle_fn = None

        self.state = "idle"
        self.cycle_count = 0
        self.log: list[dict] = []

    # ------------------------------------------------------------------
    # Perception — process input
    # ------------------------------------------------------------------

    def perceive(self, input_data, input_type="text"):
        """Process incoming information."""
        self.state = "perceiving"
        parsed = {
            "raw": input_data,
            "type": input_type,
            "timestamp": time.time(),
        }

        if isinstance(input_data, str):
            parsed["content"] = input_data
            parsed["importance"] = self._assess_importance(input_data)
        elif isinstance(input_data, dict):
            parsed["content"] = input_data
            parsed["importance"] = input_data.get("importance", 0.5)
        else:
            parsed["content"] = str(input_data)
            parsed["importance"] = 0.3

        self.working.hold(
            content=parsed,
            importance=parsed["importance"],
            tags=[input_type],
        )

        if parsed["importance"] > 0.5:
            self.episodic.store(
                content=parsed,
                importance=parsed["importance"],
                tags=[input_type, "perception"],
                context={"source": "perception"},
            )

        reflex = self.procedural.check_reflexes(input_data)
        if reflex:
            self._log("reflex", f"Triggered: {reflex['trigger']}")
            return {"type": "reflex", "action": reflex["action"],
                    "perception": parsed}

        self._fire_event("perception", parsed)
        return parsed

    # ------------------------------------------------------------------
    # Thinking — reason about a goal or question
    # ------------------------------------------------------------------

    def think(self, goal_or_question):
        """Engage reasoning to address a goal or answer a question."""
        self.state = "thinking"
        self._log("think", f"Goal: {goal_or_question}")
        self.working.hold(
            content={"thinking_about": goal_or_question},
            importance=0.8,
            tags=["thinking"],
        )
        plan = self.reasoning.reason(goal_or_question)
        consequences = self.reasoning.predict_consequence(goal_or_question)
        if consequences:
            plan["predicted_consequences"] = consequences[:3]
        self._fire_event("thought", plan)
        return plan

    # ------------------------------------------------------------------
    # Imagination — simulate scenarios
    # ------------------------------------------------------------------

    def imagine(self, scenario, steps=5):
        """Run mental simulation of a scenario."""
        self.state = "imagining"
        self._log("imagine", f"Scenario: {scenario}")
        sim_result = self.imagination.simulate(scenario, steps)
        creative = self.imagination.imagine_scenarios(seed=scenario, n=2)

        result = {
            "simulation": sim_result,
            "outcomes": sim_result.outcomes(),
            "risks": sim_result.risks(),
            "opportunities": sim_result.opportunities(),
            "creative_scenarios": creative,
        }
        self._fire_event("imagination", result)
        return result

    # ------------------------------------------------------------------
    # Learning — process experience
    # ------------------------------------------------------------------

    def learn_from(self, experience):
        """Learn from an experience."""
        self.state = "learning"
        self._log("learn", f"Experience: {experience}")

        if isinstance(experience, str):
            experience = {"observation": experience, "tags": []}
        results = self.learning.process_experience(experience)
        self._fire_event("learning", results)
        return results

    # ------------------------------------------------------------------
    # Acting — execute actions
    # ------------------------------------------------------------------

    def act(self, action, context=None):
        """Decide and execute an action."""
        self.state = "acting"
        self._log("act", f"Action: {action}")
        predictions = self.reasoning.predict_consequence(action)
        risk_check = self.imagination.simulate(action, steps=3)
        risks = risk_check.risks()

        result = {
            "action": action,
            "predictions": predictions[:3],
            "risks": risks,
            "executed": True,
            "timestamp": time.time(),
        }

        self.episodic.store(
            content=result,
            importance=0.6,
            tags=["action", str(action)],
            context={"action": action},
        )
        self._fire_event("action", result)
        return result

    # ------------------------------------------------------------------
    # Initiative — proactive behavior
    # ------------------------------------------------------------------

    def assess_situation(self):
        """Proactively assess the current situation and generate goals."""
        self.state = "assessing"
        contents = self.working.contents()
        recent = self.episodic.recent(5)
        anticipation = None
        if contents:
            last_content = str(contents[-1].content) if contents else ""
            anticipation = self.imagination.anticipate(last_content)

        new_goals = []
        if self.drives["curiosity"] > 0.6:
            new_goals.append({
                "description": "explore_unknown",
                "priority": self.drives["curiosity"],
                "source": "curiosity_drive",
            })

        if anticipation and anticipation.get("risks"):
            for risk in anticipation["risks"]:
                new_goals.append({
                    "description": f"mitigate: {risk['risk']}",
                    "priority": risk["confidence"],
                    "source": "risk_anticipation",
                })

        for goal in new_goals:
            self.add_goal(goal["description"], goal["priority"])

        return {
            "working_memory": len(contents),
            "recent_episodes": len(recent),
            "anticipation": anticipation,
            "new_goals": new_goals,
            "active_goals": list(self.goals),
        }

    def idle_cycle(self):
        """What to do when not actively processing — consolidate, imagine, plan."""
        self.state = "idle"
        self.cycle_count += 1

        consolidation = self.learning.consolidate()
        situation = self.assess_situation()

        if self.goals:
            top_goal = max(self.goals, key=lambda g: g["priority"])
            plan = self.think(top_goal["description"])
            return {"type": "goal_pursuit", "goal": top_goal, "plan": plan,
                    "consolidation": consolidation}

        return {"type": "idle", "consolidation": consolidation,
                "situation": situation}

    # ------------------------------------------------------------------
    # Goals
    # ------------------------------------------------------------------

    def add_goal(self, description, priority=0.5):
        for g in self.goals:
            if g["description"] == description:
                g["priority"] = max(g["priority"], priority)
                return g
        goal = {
            "description": description,
            "priority": priority,
            "created": time.time(),
            "status": "active",
        }
        self.goals.append(goal)
        return goal

    def complete_goal(self, description):
        for g in self.goals:
            if g["description"] == description:
                g["status"] = "completed"
                self.goals.remove(g)
                return g
        return None

    # ------------------------------------------------------------------
    # Beliefs
    # ------------------------------------------------------------------

    def believe(self, proposition, confidence=0.5):
        return self.semantic.store_belief(proposition, confidence)

    def update_belief(self, proposition, evidence, strength=2.0):
        return self.semantic.update_belief(proposition, evidence, strength)

    def get_belief(self, proposition):
        return self.semantic.get_belief(proposition)

    # ------------------------------------------------------------------
    # Knowledge
    # ------------------------------------------------------------------

    def know(self, subject, predicate, obj, confidence=1.0):
        return self.semantic.store_fact(subject, predicate, obj, confidence)

    def recall(self, query, source="all", limit=10):
        results = {}
        if source in ("all", "semantic"):
            results["semantic"] = self.semantic.recall(query, limit)
        if source in ("all", "episodic"):
            results["episodic"] = self.episodic.recall(query, limit)
        if source in ("all", "working"):
            results["working"] = self.working.find(query)
        if source in ("all", "procedural"):
            action = self.procedural.find_action(query)
            results["procedural"] = [action] if action else []
        return results

    def store_rule(self, condition, consequence, confidence=0.8):
        return self.semantic.store_rule(condition, consequence, confidence)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def on(self, event_name, handler):
        if event_name not in self.event_handlers:
            self.event_handlers[event_name] = []
        self.event_handlers[event_name].append(handler)

    def _fire_event(self, event_name, data):
        for handler in self.event_handlers.get(event_name, []):
            try:
                handler(data)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _assess_importance(self, text):
        high_importance_markers = [
            "urgent", "important", "critical", "danger", "error",
            "help", "emergency", "warning", "fail", "success",
        ]
        text_lower = text.lower()
        for marker in high_importance_markers:
            if marker in text_lower:
                return 0.9
        if text.endswith("?"):
            return 0.7
        if text.endswith("!"):
            return 0.6
        return 0.5

    def _log(self, action, detail):
        entry = {"action": action, "detail": detail,
                 "timestamp": time.time(), "cycle": self.cycle_count}
        self.log.append(entry)
        if len(self.log) > 10000:
            self.log = self.log[-5000:]

    def status(self):
        return {
            "name": self.name,
            "state": self.state,
            "cycle": self.cycle_count,
            "working_memory": len(self.working),
            "episodic_memory": len(self.episodic),
            "semantic_knowledge": self.semantic.concepts_count(),
            "beliefs": len(self.semantic.beliefs),
            "rules": len(self.semantic.rules),
            "procedures": len(self.procedural.procedures),
            "goals": len(self.goals),
            "drives": dict(self.drives),
        }

    def __repr__(self):
        return (f"Mind({self.name!r}, state={self.state}, "
                f"memories={len(self.episodic)}, "
                f"knowledge={self.semantic.concepts_count()})")
