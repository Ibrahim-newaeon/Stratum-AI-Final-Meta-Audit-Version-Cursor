import { render, screen } from '@testing-library/react';
import { MemoryRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

function ConnectPlatformsRedirect() {
  const { search } = useLocation();
  return <Navigate to={`/dashboard/campaigns/connect${search}`} replace />;
}

function ConnectPage() {
  const { search } = useLocation();
  return <div>Connect Platforms{search}</div>;
}

describe('legacy /connect route', () => {
  it('redirects to the dashboard connect page and keeps OAuth query params', () => {
    render(
      <MemoryRouter initialEntries={['/connect?platform=meta&status=success']}>
        <Routes>
          <Route path="/connect" element={<ConnectPlatformsRedirect />} />
          <Route path="/dashboard/campaigns/connect" element={<ConnectPage />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByText('Connect Platforms?platform=meta&status=success')).toBeTruthy();
  });
});
