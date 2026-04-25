// All client-visible env vars are baked at build time. NEXT_PUBLIC_API_BASE_URL
// points at the FastAPI backend; production deploys override this in the
// build step (see .github/workflows/deploy-dev.yml).
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export const APP_ENV = process.env.NEXT_PUBLIC_APP_ENV || "development";
