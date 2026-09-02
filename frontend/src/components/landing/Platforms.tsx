// Midnight Teal theme
const theme = {
  primary: '#00c7be',
  primaryLight: 'rgba(0, 199, 190, 0.15)',
  gold: '#e2b347',
  goldLight: 'rgba(226, 179, 71, 0.15)',
  bgBase: '#0b1215',
  bgCard: 'rgba(255, 255, 255, 0.05)',
  textPrimary: '#FFFFFF',
  textSecondary: 'rgba(255, 255, 255, 0.7)',
  textMuted: 'rgba(255, 255, 255, 0.5)',
  border: 'rgba(255, 255, 255, 0.08)',
};

export function Platforms() {
  const platforms = [
    {
      name: 'Meta',
      api: 'Custom Audiences API',
      icon: MetaIcon,
      iconColor: 'rgba(255, 255, 255, 0.7)',
      brandHex: '#0866FF',
      gradientBg: 'linear-gradient(135deg, rgba(8,102,255,0.08), rgba(8,102,255,0.02))',
    },
    {
      name: 'Facebook',
      api: 'Marketing API',
      icon: FacebookIcon,
      iconColor: 'rgba(255, 255, 255, 0.7)',
      brandHex: '#1877F2',
      gradientBg: 'linear-gradient(135deg, rgba(24,119,242,0.08), rgba(24,119,242,0.02))',
    },
    {
      name: 'Instagram',
      api: 'Instagram Graph API',
      icon: InstagramIcon,
      iconColor: 'rgba(255, 255, 255, 0.7)',
      brandHex: '#E4405F',
      gradientBg: 'linear-gradient(135deg, rgba(228,64,95,0.08), rgba(228,64,95,0.02))',
    },
    {
      name: 'WhatsApp',
      api: 'WhatsApp Business API',
      icon: WhatsAppIcon,
      iconColor: 'rgba(255, 255, 255, 0.7)',
      brandHex: '#25D366',
      gradientBg: 'linear-gradient(135deg, rgba(37,211,102,0.08), rgba(37,211,102,0.02))',
    },
  ];

  return (
    <section className="py-24" style={{ background: theme.bgBase, borderTop: `1px solid ${theme.border}`, borderBottom: `1px solid ${theme.border}` }}>
      <div className="max-w-7xl mx-auto px-6">
        {/* Section Header - Centered */}
        <div className="text-center mb-12">
          <div className="flex justify-center mb-6">
            <div
              className="inline-flex items-center gap-2 px-4 py-2 rounded-full"
              style={{
                background: theme.primaryLight,
                border: '1px solid rgba(0, 199, 190, 0.3)',
              }}
            >
              <span className="text-sm font-medium" style={{ color: theme.primary }}>
                Platform Integrations
              </span>
            </div>
          </div>
          <h2 className="text-3xl md:text-4xl font-bold text-white mb-4 text-center">
            One source of truth. <span style={{ color: theme.primary }}>Every platform.</span>
          </h2>
          <p className="text-lg text-center" style={{ color: theme.textMuted }}>
            Unified Across Your Entire Ad Stack
          </p>
        </div>

        {/* Platform Cards Grid */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-6 max-w-5xl mx-auto">
          {platforms.map((platform) => (
            <div
              key={platform.name}
              className="group flex flex-col items-center justify-center p-8 rounded-2xl transition-all duration-300 hover:-translate-y-1 relative overflow-hidden"
              style={{
                background: platform.gradientBg,
                backdropFilter: 'blur(40px)',
                WebkitBackdropFilter: 'blur(40px)',
                border: `1px solid rgba(255,255,255,0.08)`,
                boxShadow: 'inset 0 1px 1px rgba(255,255,255,0.08), 0 2px 12px rgba(0,0,0,0.15)',
              }}
            >
              {/* Brand accent top bar */}
              <div
                className="absolute top-0 left-0 right-0 h-[3px] rounded-t-2xl"
                style={{ background: platform.brandHex }}
              />

              {/* Icon Container */}
              <div
                className="w-16 h-16 rounded-2xl flex items-center justify-center mb-4"
                style={{
                  background: 'rgba(255, 255, 255, 0.05)',
                  border: `1px solid ${theme.border}`,
                }}
              >
                <platform.icon
                  className="w-10 h-10"
                  style={{ color: platform.iconColor }}
                />
              </div>

              {/* Platform Name */}
              <span
                className="text-base font-medium text-white mb-1 text-center"
              >
                {platform.name}
              </span>

              {/* API Description */}
              <span
                className="text-xs text-center leading-tight"
                style={{ color: theme.textMuted }}
              >
                {platform.api}
              </span>
            </div>
          ))}
        </div>

        {/* Stats Section */}
        <div className="mt-16 flex flex-wrap items-center justify-center gap-8 text-center">
          <div className="px-8 py-4" style={{ borderRight: `1px solid ${theme.border}` }}>
            <div className="text-3xl font-bold text-white">4B+</div>
            <div className="text-sm" style={{ color: theme.textMuted }}>Events Processed</div>
          </div>
          <div className="px-8 py-4" style={{ borderRight: `1px solid ${theme.border}` }}>
            <div className="text-3xl font-bold text-white">$2.1B</div>
            <div className="text-sm" style={{ color: theme.textMuted }}>Ad Spend Managed</div>
          </div>
          <div className="px-8 py-4">
            <div className="text-3xl font-bold text-white">99.9%</div>
            <div className="text-sm" style={{ color: theme.textMuted }}>Uptime SLA</div>
          </div>
        </div>
      </div>
    </section>
  );
}

// Platform Icons - Monochrome style
function MetaIcon({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return (
    <svg className={className} style={style} viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2.04c-5.5 0-10 4.49-10 10.02 0 5 3.66 9.15 8.44 9.9v-7H7.9v-2.9h2.54V9.85c0-2.51 1.49-3.89 3.78-3.89 1.09 0 2.23.19 2.23.19v2.47h-1.26c-1.24 0-1.63.77-1.63 1.56v1.88h2.78l-.45 2.9h-2.33v7a10 10 0 0 0 8.44-9.9c0-5.53-4.5-10.02-10-10.02Z" />
    </svg>
  );
}

function FacebookIcon({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return (
    <svg className={className} style={style} viewBox="0 0 24 24" fill="currentColor">
      <path d="M24 12.07C24 5.4 18.63 0 12 0S0 5.4 0 12.07C0 18.1 4.39 23.09 10.13 24v-8.44H7.08v-3.49h3.04V9.41c0-3.02 1.8-4.7 4.54-4.7 1.31 0 2.68.24 2.68.24v2.97h-1.5c-1.5 0-1.96.93-1.96 1.89v2.26h3.32l-.53 3.49h-2.8V24C19.62 23.09 24 18.1 24 12.07z" />
    </svg>
  );
}

function InstagramIcon({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return (
    <svg className={className} style={style} viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2.16c3.2 0 3.58.01 4.85.07 1.17.05 1.8.25 2.23.41.56.22.96.48 1.38.9.42.42.68.82.9 1.38.16.42.35 1.06.41 2.23.06 1.27.07 1.65.07 4.85s-.01 3.58-.07 4.85c-.05 1.17-.25 1.8-.41 2.23-.22.56-.48.96-.9 1.38-.42.42-.82.68-1.38.9-.42.16-1.06.35-2.23.41-1.27.06-1.65.07-4.85.07s-3.58-.01-4.85-.07c-1.17-.05-1.8-.25-2.23-.41a3.72 3.72 0 0 1-1.38-.9c-.42-.42-.68-.82-.9-1.38-.16-.42-.35-1.06-.41-2.23-.06-1.27-.07-1.65-.07-4.85s.01-3.58.07-4.85c.05-1.17.25-1.8.41-2.23.22-.56.48-.96.9-1.38.42-.42.82-.68 1.38-.9.42-.16 1.06-.35 2.23-.41 1.27-.06 1.65-.07 4.85-.07M12 0C8.74 0 8.33.01 7.05.07 5.78.13 4.9.33 4.14.63a5.9 5.9 0 0 0-2.13 1.38A5.9 5.9 0 0 0 .63 4.14C.33 4.9.13 5.78.07 7.05.01 8.33 0 8.74 0 12s.01 3.67.07 4.95c.06 1.27.26 2.15.56 2.91.31.8.72 1.47 1.38 2.13a5.9 5.9 0 0 0 2.13 1.38c.76.3 1.64.5 2.91.56C8.33 23.99 8.74 24 12 24s3.67-.01 4.95-.07c1.27-.06 2.15-.26 2.91-.56a5.9 5.9 0 0 0 2.13-1.38 5.9 5.9 0 0 0 1.38-2.13c.3-.76.5-1.64.56-2.91.06-1.28.07-1.69.07-4.95s-.01-3.67-.07-4.95c-.06-1.27-.26-2.15-.56-2.91a5.9 5.9 0 0 0-1.38-2.13A5.9 5.9 0 0 0 19.86.63c-.76-.3-1.64-.5-2.91-.56C15.67.01 15.26 0 12 0zm0 5.84A6.16 6.16 0 1 0 18.16 12 6.16 6.16 0 0 0 12 5.84zm0 10.15A3.99 3.99 0 1 1 16 12a3.99 3.99 0 0 1-4 3.99zm7.85-10.4a1.44 1.44 0 1 1-1.44-1.44 1.44 1.44 0 0 1 1.44 1.44z" />
    </svg>
  );
}

function WhatsAppIcon({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return (
    <svg className={className} style={style} viewBox="0 0 24 24" fill="currentColor">
      <path d="M17.47 14.38c-.3-.15-1.76-.87-2.03-.97-.27-.1-.47-.15-.67.15-.2.3-.77.97-.94 1.17-.17.2-.35.22-.64.07-.3-.15-1.26-.46-2.4-1.48-.88-.79-1.48-1.76-1.65-2.06-.17-.3-.02-.46.13-.61.13-.13.3-.35.44-.52.15-.17.2-.3.3-.5.1-.2.05-.37-.03-.52-.07-.15-.67-1.61-.92-2.2-.24-.58-.48-.5-.67-.51h-.57c-.2 0-.52.07-.8.37-.27.3-1.04 1.02-1.04 2.48s1.07 2.88 1.22 3.08c.15.2 2.1 3.21 5.1 4.5.71.31 1.27.49 1.7.63.72.23 1.37.2 1.88.12.58-.09 1.76-.72 2.01-1.42.25-.7.25-1.29.17-1.42-.07-.13-.27-.2-.57-.35zM12.05 21.79h-.01a9.87 9.87 0 0 1-5.03-1.38l-.36-.21-3.74.98 1-3.65-.24-.37a9.86 9.86 0 0 1-1.51-5.26c0-5.45 4.44-9.88 9.9-9.88a9.82 9.82 0 0 1 6.99 2.9 9.82 9.82 0 0 1 2.9 7c-.01 5.45-4.45 9.87-9.9 9.87zm8.42-18.3A11.8 11.8 0 0 0 12.05 0C5.5 0 .16 5.34.16 11.9c0 2.1.55 4.14 1.59 5.95L.06 24l6.3-1.65a11.87 11.87 0 0 0 5.68 1.45h.01c6.55 0 11.89-5.34 11.89-11.9a11.82 11.82 0 0 0-3.47-8.41z" />
    </svg>
  );
}
