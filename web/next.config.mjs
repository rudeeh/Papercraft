/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The browser talks to the API through this origin, so a deployment can
  // point the whole app at a different backend without a rebuild of the
  // client bundle's fetch URLs.
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000',
  },
};

export default nextConfig;
