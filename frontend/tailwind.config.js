/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  // 关闭 preflight，避免与 Ant Design v5 的组件样式重置冲突
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        brand: '#185fa5',
        'brand-700': '#124a82',
        'brand-soft': '#e6f1fb',
        ink: '#1f2329',
        // 次级文字提升到 slate-600，满足 WCAG AA 正文 4.5:1 对比度
        muted: '#475569',
        'muted-soft': '#64748b',
        line: '#e5e7eb',
        'line-strong': '#d8dee6',
        bg: '#f4f6f9',
        ok: '#0f6e56',
        warn: '#854f0b',
        danger: '#a32d2d',
      },
      borderRadius: {
        card: '12px',
        lg: '10px',
      },
      boxShadow: {
        card: '0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.06)',
        soft: '0 6px 20px rgba(16,24,40,0.08)',
        float: '0 10px 34px rgba(16,24,40,0.12)',
      },
    },
  },
  plugins: [],
}
