import type { NextConfig } from "next";

const config: NextConfig = {
  // We ship a static export to S3 + CloudFront for MVP. All dynamic data comes from the FastAPI.
  // Switch to "standalone" later if we need SSR (Amplify Hosting or Lambda@Edge).
  output: "export",
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
  reactStrictMode: true,
};

export default config;
