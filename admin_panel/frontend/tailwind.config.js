/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: '#08080d',
        surface: '#131320',
        surface2: '#1b1b2c',
        edge: '#28283d',
        accent: {
          DEFAULT: '#22d3ee',
          green: '#34d399',
        },
        danger: '#f87171',
        warn: '#fbbf24',
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(34,211,238,0.15), 0 0 24px -4px rgba(34,211,238,0.35)',
        'glow-green': '0 0 0 1px rgba(52,211,153,0.2), 0 0 20px -4px rgba(52,211,153,0.45)',
        card: '0 1px 0 0 rgba(255,255,255,0.03) inset, 0 8px 24px -12px rgba(0,0,0,0.6)',
      },
      backgroundImage: {
        'radial-fade': 'radial-gradient(circle at top left, rgba(34,211,238,0.08), transparent 60%)',
      },
      keyframes: {
        'fade-in': {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'pulse-live': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.4' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.25s ease-out',
        'pulse-live': 'pulse-live 1.6s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
