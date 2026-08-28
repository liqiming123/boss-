import{defineConfig}from'@playwright/test';
export default defineConfig({testDir:'./tests/e2e',timeout:30000,workers:1,fullyParallel:false,webServer:[{command:'PYTHONPATH=../api/src ../../.venv/bin/python -m recruitment_collab.infrastructure.seed && PYTHONPATH=../api/src ../../.venv/bin/python -m uvicorn recruitment_collab.main:app --port 8000',url:'http://localhost:8000/api/v1/health',reuseExistingServer:true,timeout:30000},{command:'pnpm --filter @recruitment/mock-site dev',url:'http://localhost:5174',reuseExistingServer:true,timeout:30000}],reporter:'list'});

