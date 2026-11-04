import { it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { GoalSheet } from './GoalSheet';
it('goal sheet creates goal', async () => {
  const create = vi.fn(); render(<GoalSheet projects={['p']} tools={['claude']} onCreate={create} onClose={() => {}} />);
  fireEvent.change(screen.getByLabelText('Goal'), { target: { value: 'Build it' } });
  fireEvent.click(screen.getByText('Create goal'));
  await waitFor(() => expect(create).toHaveBeenCalled());
});
