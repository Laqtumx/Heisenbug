/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // Proxy API calls to the FastAPI service so the browser talks to one
    // origin. This keeps the auth token out of cross-origin requests and means
    // the frontend needs no CORS entry of its own in production.
    const api = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};

export default nextConfig;
