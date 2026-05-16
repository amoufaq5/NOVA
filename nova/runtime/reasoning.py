"""Nova Reasoning Engine.

Handles logical inference, causal reasoning, analogy, consequence prediction.
Operates over semantic memory (knowledge graph) and episodic memory (experience).
"""

from .memory import SemanticMemory, EpisodicMemory, MemoryItem


class ReasoningEngine:
    """Reasons over knowledge and experience to draw conclusions, make plans,
    and predict consequences."""

    def __init__(self, semantic: SemanticMemory, episodic: EpisodicMemory):
        self.semantic = semantic
        self.episodic = episodic
        self.inference_trace: list[dict] = []

    def reason(self, goal, context=None):
        """Main reasoning entry point. Given a goal, produce a plan or conclusion."""
        self.inference_trace = []
        relevant_knowledge = self.semantic.recall(str(goal), limit=20)
        past_experience = self.episodic.similar(str(goal), limit=5)
        applicable_rules = self.semantic.matching_rules(str(goal))

        plan = {
            "goal": goal,
            "knowledge_used": len(relevant_knowledge),
            "experience_used": len(past_experience),
            "rules_applied": len(applicable_rules),
            "steps": [],
            "confidence": 0.5,
        }

        if applicable_rules:
            for rule in applicable_rules[:3]:
                step = {
                    "type": "rule_application",
                    "rule": f"IF {rule['if']} THEN {rule['then']}",
                    "confidence": rule["confidence"],
                }
                plan["steps"].append(step)
                self.inference_trace.append(step)

        if past_experience:
            for ep in past_experience[:3]:
                step = {
                    "type": "experience_based",
                    "episode": str(ep.content)[:100],
                    "relevance": ep.importance,
                }
                plan["steps"].append(step)
                self.inference_trace.append(step)

        if plan["steps"]:
            confidences = [s.get("confidence", s.get("relevance", 0.5))
                          for s in plan["steps"]]
            plan["confidence"] = sum(confidences) / len(confidences)

        return plan

    def infer(self, premise):
        """Forward-chain inference: given a premise, what can we conclude?"""
        conclusions = []
        rules = self.semantic.matching_rules(str(premise))
        for rule in rules:
            conclusions.append({
                "conclusion": rule["then"],
                "confidence": rule["confidence"],
                "basis": f"IF {rule['if']} THEN {rule['then']}",
            })
        return conclusions

    def explain(self, observation):
        """Abductive reasoning: given an observation, what caused it?"""
        causes = []
        for rel in self.semantic.relations:
            if rel["predicate"] in ("causes", "leads_to", "produces"):
                if str(observation).lower() in str(rel["object"]).lower():
                    causes.append({
                        "cause": rel["subject"],
                        "mechanism": rel["predicate"],
                        "confidence": rel["confidence"],
                    })
        past = self.episodic.recall(str(observation), limit=5)
        for ep in past:
            if ep.context.get("cause"):
                causes.append({
                    "cause": ep.context["cause"],
                    "mechanism": "past_experience",
                    "confidence": ep.importance,
                })

        causes.sort(key=lambda c: -c["confidence"])
        return causes

    def predict_consequence(self, action, context=None):
        """Predict what will happen if an action is taken."""
        predictions = []
        rules = self.semantic.matching_rules(str(action))
        for rule in rules:
            predictions.append({
                "outcome": rule["then"],
                "confidence": rule["confidence"],
                "source": "rule",
            })

        past = self.episodic.recall(str(action), limit=10)
        for ep in past:
            if ep.context.get("outcome"):
                predictions.append({
                    "outcome": ep.context["outcome"],
                    "confidence": ep.importance,
                    "source": "experience",
                })

        predictions.sort(key=lambda p: -p["confidence"])
        return predictions

    def check_consistency(self, new_belief, existing_beliefs=None):
        """Check if a new belief is consistent with existing knowledge."""
        if existing_beliefs is None:
            existing_beliefs = self.semantic.beliefs
        conflicts = []
        new_str = str(new_belief).lower()
        for prop, belief in existing_beliefs.items():
            if belief["confidence"] > 0.7:
                prop_lower = prop.lower()
                if ("not " + new_str in prop_lower or
                        new_str + " not" in prop_lower or
                        "no " + new_str in prop_lower):
                    conflicts.append({
                        "conflicting_belief": prop,
                        "confidence": belief["confidence"],
                    })
        return {"consistent": len(conflicts) == 0, "conflicts": conflicts}

    def analogy(self, source, target):
        """Analogical reasoning: find structural similarities between domains."""
        source_rels = self.semantic.get_related(str(source), depth=2)
        target_rels = self.semantic.get_related(str(target), depth=2)

        source_predicates = {r[1] for r in source_rels}
        target_predicates = {r[1] for r in target_rels}
        shared = source_predicates & target_predicates

        mappings = []
        for pred in shared:
            source_facts = [(s, o) for s, p, o in source_rels if p == pred]
            target_facts = [(s, o) for s, p, o in target_rels if p == pred]
            if source_facts and target_facts:
                mappings.append({
                    "relation": pred,
                    "source_example": source_facts[0],
                    "target_example": target_facts[0],
                })

        return {
            "source": source, "target": target,
            "shared_structure": len(shared),
            "mappings": mappings,
            "strength": len(shared) / max(len(source_predicates | target_predicates), 1),
        }

    def __repr__(self):
        return f"ReasoningEngine(trace={len(self.inference_trace)} steps)"
