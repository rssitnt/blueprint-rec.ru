import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@blueprint-rec/shared-types"],
  experimental: {
    devtoolSegmentExplorer: false
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8010/api/:path*",
      },
      {
        source: "/storage/:path*",
        destination: "http://127.0.0.1:8010/storage/:path*",
      },
    ];
  },
};

export default nextConfig;
