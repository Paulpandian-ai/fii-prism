-- Enable required extensions at database init (idempotent).
-- pgvector for embedding storage; pg_trgm for fuzzy ticker search; uuid-ossp for UUID generation.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
