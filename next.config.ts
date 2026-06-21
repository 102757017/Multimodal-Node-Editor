import type { NextConfig } from "next";

// Backend URL — change this if you run the backend on a different port.
// Can be overridden with the BACKEND_URL environment variable.
const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:3030";

const nextConfig: NextConfig = {
  output: "standalone",
  typescript: {
    ignoreBuildErrors: true,
  },
  reactStrictMode: false,
  async rewrites() {
    // Proxy all /api/* requests to the FastAPI backend so the frontend works
    // both through the Caddy gateway (port 81, which strips XTransformPort
    // and routes directly) AND when accessing Next.js directly (port 3000,
    // e.g. on a local dev machine without Caddy).
    //
    // The ?XTransformPort=3030 query param added by api.ts is harmless to the
    // backend and is simply forwarded.
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_URL}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
