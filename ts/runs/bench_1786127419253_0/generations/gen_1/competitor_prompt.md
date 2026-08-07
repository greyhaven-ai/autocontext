Describe your strategy for the grid_ctf scenario. Return JSON with the strategy parameters.

Scenario Rules:
20x20 capture-the-flag map with fog of war and three unit archetypes (Scout, Soldier, Commander). Preserve at least one defender near base.

Strategy Interface:
Return JSON object with keys `aggression`, `defense`, and `path_bias`, all floats in [0,1]. Constraint: aggression + defense <= 1.4.

Evaluation Criteria:
Primary objective is capture progress. Secondary objectives are defender survivability and resource efficiency.

Current Playbook:
No playbook yet. Start from scenario rules and observation.

Respond with JSON only. Include the strategy fields required by the strategy interface.
