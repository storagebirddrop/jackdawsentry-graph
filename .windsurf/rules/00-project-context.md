## 0 Jackdaw Sentry Graph Project Context

You are working on the standalone Jackdaw Sentry graph product.
This is the canonical repo for all new graph-product work.

Architecture:
- Backend: FastAPI (Python)
- Frontend: React + TypeScript + Vite
- Graph/data runtime: Neo4j + PostgreSQL + Redis
- Proxy: Nginx

Priority:
- graph product quality
- clean public-facing contracts
- minimal regressions
- public-safe docs and defaults

Boundary rule:
- If the graph still depends on code that only exists in the private
  `jackdawsentry` repo, migrate it here, duplicate the minimal graph-safe
  primitive, or leave a thin private adapter there.
- Do not move new graph feature ownership back into the private repo.

Always prioritize correctness > readability > performance.
