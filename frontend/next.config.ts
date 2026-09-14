import path from "node:path";
import type { NextConfig } from "next";

const API = process.env.BACKEND_URL || "http://127.0.0.1:5050";

const nextConfig: NextConfig = {
  turbopack: { root: path.join(__dirname) },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/report/:path*", destination: `${API}/report/:path*` },
      { source: "/health", destination: `${API}/health` },
    ];
  },
};

export default nextConfig;
