import type { NextConfig } from "next";

const api = process.env.PLOT_API_URL || "http://127.0.0.1:8000";

// External hosts the browser may contact: basemap tiles and public WMS overlays only.
const tileHosts = [
  "https://tiles.openfreemap.org",
  "https://server.arcgisonline.com",
  "https://environment.data.gov.uk",
  "https://map.bgs.ac.uk",
].join(" ");

const csp = [
  "default-src 'self'",
  `img-src 'self' data: blob: ${tileHosts}`,
  `connect-src 'self' ${tileHosts}`,
  "style-src 'self' 'unsafe-inline'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  "worker-src 'self' blob:",
  "font-src 'self' data:",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(self)" },
        ],
      },
    ];
  },
};

export default config;
