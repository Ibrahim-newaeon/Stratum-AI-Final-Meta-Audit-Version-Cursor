import { useNavigate } from 'react-router-dom';
import type { LoginResult } from '@/contexts/AuthContext';

interface DemoTheme {
  primary: string;
  textMuted: string;
  bgCard: string;
  border: string;
  borderHover: string;
}

interface DevDemoAccessProps {
  login: (email: string, password: string) => Promise<LoginResult>;
  from: string;
  isLoading: boolean;
  setIsLoading: (value: boolean) => void;
  setError: (value: string) => void;
  setEmail: (value: string) => void;
  setPassword: (value: string) => void;
  theme: DemoTheme;
}

/**
 * Local-only demo buttons. This module is imported from Login only inside an
 * `import.meta.env.DEV` branch so production JS and source maps omit it.
 *
 * Passwords come from Vite env (frontend/.env), not source — must match the
 * accounts created by backend/scripts_seed_demo.py.
 */
export default function DevDemoAccess({
  login,
  from,
  isLoading,
  setIsLoading,
  setError,
  setEmail,
  setPassword,
  theme,
}: DevDemoAccessProps) {
  const navigate = useNavigate();

  const handleDemoLogin = async (role: 'superadmin' | 'admin' | 'user') => {
    const email = role === 'superadmin' ? 'superadmin@stratum.ai' : 'demo@stratum.ai';
    const password =
      role === 'superadmin'
        ? import.meta.env.VITE_DEV_SUPERADMIN_PASSWORD
        : import.meta.env.VITE_DEV_DEMO_PASSWORD;

    if (typeof password !== 'string' || !password) {
      setError(
        'Demo shortcuts need VITE_DEV_DEMO_PASSWORD and VITE_DEV_SUPERADMIN_PASSWORD in frontend/.env (same values as backend/scripts_seed_demo.py).',
      );
      return;
    }

    setEmail(email);
    setPassword(password);
    setError('');
    setIsLoading(true);

    try {
      const result = await login(email, password);
      if (result.success) {
        navigate(from, { replace: true });
      } else {
        setError(result.error || 'Login failed');
      }
    } catch {
      setError('An unexpected error occurred');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="mt-6 pt-6" style={{ borderTop: `1px solid ${theme.border}` }}>
      <p className="text-center text-xs mb-3" style={{ color: theme.textMuted }}>
        Quick Demo Access
      </p>
      <div className="grid grid-cols-3 gap-2">
        {(['superadmin', 'admin', 'user'] as const).map((role) => (
          <button
            key={role}
            type="button"
            onClick={() => handleDemoLogin(role)}
            disabled={isLoading}
            className="px-3 py-2 rounded-xl text-xs font-medium capitalize transition-all duration-200 disabled:opacity-50"
            style={{
              background: theme.bgCard,
              backdropFilter: 'blur(40px)',
              border: `1px solid ${theme.border}`,
              color: theme.textMuted,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = theme.borderHover;
              e.currentTarget.style.color = theme.primary;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = theme.border;
              e.currentTarget.style.color = theme.textMuted;
            }}
          >
            {role === 'superadmin' ? 'Super' : role}
          </button>
        ))}
      </div>
    </div>
  );
}
