/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        savencia: {
          primary: '#0066B3',
          'primary-dark': '#004d86',
          'primary-light': '#3385c2',
          secondary: '#00a0dc',
          accent: '#f7941d',
          success: '#28a745',
          warning: '#ffc107',
          danger: '#dc3545',
        },
        adoption: {
          emerging: '#fbbf24',      // yellow-400
          growing: '#f97316',       // orange-500
          mainstream: '#22c55e',    // green-500
          declining: '#ef4444',     // red-500
        }
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
