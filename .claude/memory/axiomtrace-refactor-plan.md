---
name: axiomtrace-refactor-plan
description: Implementation plan for the AST parsing overhaul directive
metadata:
  type: project
---

## Directive: Intelligent Hierarchical Code Parser & Enriched Graph

### Backend changes (`backend/services/ast_service.py`)
- Replace statement-level noise nodes with domain-block nodes (one per function/class/import)
- Filter: trivial assignments, empty blocks, punctuation, low-level variable tokens
- Semantic types: `entrypoint`, `service`, `database`, `utility`, `error_handler` (depth-layered for dagre)
- Enriched metadata per node: `category`, `complexity`, `description`, `parameters`, `returns`
- Keep architecture graph functions unchanged
- Update `ASTParserService`, `VALID_TYPES`, `GraphBuilder.add_node`, `_build_python_graph`, `_build_js_graph`

### Files to update
1. `backend/services/ast_service.py` — rewrite parsing section
2. `backend/controllers/parse_controller.py` — `_VALID_NODE_TYPES` -> new types
3. `backend/schemas/node_schema.py` — `VALID_NODE_TYPES` -> new types
4. `frontend/src/config/theme.js` — `SEMANTIC_TYPES`, `TYPE_META`, `LAYOUT` (nodeSep:80, rankSep:100)
5. `frontend/src/components/canvas/CustomNode.jsx` — ICONS for new types, light-mono styling
6. `frontend/src/components/canvas/Inspector.jsx` — show enriched metadata, new type selector
7. `frontend/src/hooks/useGraphLayout.js` — dagre nodeSep:80, rankSep:100
8. `backend/tests/test_parser.py` — update expectations for new contract

### Semantic type -> depth mapping
entrypoint:0, service:1, database:2, utility:3, error_handler:3