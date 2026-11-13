/** @type {import('next').NextConfig} */
const api = process.env.API_INTERNAL_URL; // set in the all-in-one container: proxy /api to FastAPI
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  async rewrites() {
    return api ? [{ source: "/api/:path*", destination: `${api}/api/:path*` }] : [];
  },
};
export default nextConfig;
