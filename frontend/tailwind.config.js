/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  // The existing dashboard owns its visual reset. Keeping Preflight off makes
  // Tailwind available for future shadcn primitives without changing Part A UI.
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        primary: "var(--cyan)",
        "primary-foreground": "var(--void)",
        ring: "var(--ring)",
        destructive: "var(--red)",
      },
    },
  },
  plugins: [],
};
