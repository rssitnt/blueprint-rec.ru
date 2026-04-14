import type { NextConfig } from "next";

const annotationUpstreamBaseUrl = (
  process.env.ANNOTATION_UPSTREAM_BASE_URL ??
  process.env.NEXT_PUBLIC_ANNOTATION_API_BASE_URL ??
  "http://127.0.0.1:8010"
).replace(/\/$/, "");

const nextConfig: NextConfig = {
  transpilePackages: ["@blueprint-rec/shared-types"],
  experimental: {
    devtoolSegmentExplorer: false
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${annotationUpstreamBaseUrl}/api/:path*`,
      },
      {
        source: "/storage/:path*",
        destination: `${annotationUpstreamBaseUrl}/storage/:path*`,
      },
    ];
  },
};

export default nextConfig;
