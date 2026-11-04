import { it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PlanEditor } from './PlanEditor';
it('plan editor validates', () => { render(<PlanEditor steps={[]} onChange={vi.fn()} />); expect(screen.getByText(/at least one step/i)).toBeInTheDocument(); });
it('start disabled until plan valid', () => { render(<PlanEditor steps={[]} onChange={vi.fn()} onStart={vi.fn()} />); expect(screen.getByText('Start plan')).toBeDisabled(); });
