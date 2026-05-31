/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class', // supports explicit dark theme overlays
  theme: {
    extend: {
      colors: {
        // Premium curated dark-mode HSL color mapping
        retail: {
          950: '#030712',
          900: '#111827',
          850: '#1f2937',
          800: '#374151',
          700: '#4b5563',
          300: '#d1d5db',
          100: '#f3f4f6',
        }
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      }
    },
  },
  plugins: [],
}
