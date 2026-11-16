import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Docker: build ra .next/standalone (server.js + node_modules tối thiểu).
  output: "standalone",
  allowedDevOrigins: ["10.10.3.178"],
};

export default nextConfig;
