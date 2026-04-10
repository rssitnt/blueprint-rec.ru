import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}"
  ],
  theme: {
    extend: {
      colors: {
        paper: "#fffdf7",
        graphite: "#232733",
        ink: "#0f1220",
        line: "#ece7d8"
      },
      borderRadius: {
        xl: "1.2rem"
      },
      boxShadow: {
        card: "0 22px 40px rgba(38, 46, 79, 0.08)"
      }
    }
  },
  plugins: []
};

export default config;
