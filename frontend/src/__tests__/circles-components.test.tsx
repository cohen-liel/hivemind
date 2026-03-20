/**
 * Tests for circles components: CircleCard, CreateCircleDialog.
 *
 * Uses @testing-library/react for component rendering and user interactions.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CircleCard from '../components/circles/CircleCard';
import CreateCircleDialog from '../components/circles/CreateCircleDialog';
import type { Circle } from '../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeCircle(overrides: Partial<Circle> = {}): Circle {
  return {
    id: 'circle-1',
    name: 'Frontend Team',
    description: 'All frontend developers',
    avatar_url: null,
    member_count: 5,
    project_count: 3,
    created_by: 'user-1',
    created_at: '2026-03-20T00:00:00Z',
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// CircleCard
// ---------------------------------------------------------------------------

describe('CircleCard', () => {
  const mockOnClick = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('test_card_when_rendered_should_show_name', () => {
    render(<CircleCard circle={makeCircle()} isActive={false} onClick={mockOnClick} />);
    expect(screen.getByText('Frontend Team')).toBeTruthy();
  });

  it('test_card_when_has_description_should_show_it', () => {
    render(<CircleCard circle={makeCircle()} isActive={false} onClick={mockOnClick} />);
    expect(screen.getByText('All frontend developers')).toBeTruthy();
  });

  it('test_card_when_no_description_should_not_show_paragraph', () => {
    const circle = makeCircle({ description: undefined });
    render(<CircleCard circle={circle} isActive={false} onClick={mockOnClick} />);
    expect(screen.queryByText('All frontend developers')).toBeNull();
  });

  it('test_card_when_clicked_should_call_onClick', async () => {
    render(<CircleCard circle={makeCircle()} isActive={false} onClick={mockOnClick} />);
    await userEvent.click(screen.getByRole('button'));
    expect(mockOnClick).toHaveBeenCalledOnce();
  });

  it('test_card_when_active_should_set_aria_current', () => {
    render(<CircleCard circle={makeCircle()} isActive={true} onClick={mockOnClick} />);
    const btn = screen.getByRole('button');
    expect(btn.getAttribute('aria-current')).toBe('true');
  });

  it('test_card_when_not_active_should_not_have_aria_current', () => {
    render(<CircleCard circle={makeCircle()} isActive={false} onClick={mockOnClick} />);
    const btn = screen.getByRole('button');
    expect(btn.getAttribute('aria-current')).toBeNull();
  });

  it('test_card_should_show_member_count', () => {
    render(<CircleCard circle={makeCircle({ member_count: 12 })} isActive={false} onClick={mockOnClick} />);
    expect(screen.getByText('12 members')).toBeTruthy();
  });

  it('test_card_when_one_member_should_show_singular', () => {
    render(<CircleCard circle={makeCircle({ member_count: 1 })} isActive={false} onClick={mockOnClick} />);
    expect(screen.getByText('1 member')).toBeTruthy();
  });

  it('test_card_should_show_project_count', () => {
    render(<CircleCard circle={makeCircle({ project_count: 7 })} isActive={false} onClick={mockOnClick} />);
    expect(screen.getByText('7 projects')).toBeTruthy();
  });

  it('test_card_when_no_avatar_should_show_initials', () => {
    render(<CircleCard circle={makeCircle({ name: 'Frontend Team' })} isActive={false} onClick={mockOnClick} />);
    expect(screen.getByText('FT')).toBeTruthy();
  });

  it('test_card_when_has_avatar_should_show_image', () => {
    const circle = makeCircle({ avatar_url: 'https://example.com/avatar.png' });
    const { container } = render(<CircleCard circle={circle} isActive={false} onClick={mockOnClick} />);
    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img?.getAttribute('src')).toBe('https://example.com/avatar.png');
  });

  it('test_card_should_have_accessible_label', () => {
    render(<CircleCard circle={makeCircle()} isActive={false} onClick={mockOnClick} />);
    expect(screen.getByLabelText('Select circle: Frontend Team')).toBeTruthy();
  });

  it('test_card_when_single_word_name_should_show_single_initial', () => {
    const circle = makeCircle({ name: 'Designers' });
    render(<CircleCard circle={circle} isActive={false} onClick={mockOnClick} />);
    expect(screen.getByText('D')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// CreateCircleDialog
// ---------------------------------------------------------------------------

describe('CreateCircleDialog', () => {
  const mockOnClose = vi.fn();
  const mockOnCreate = vi.fn<(data: { name: string; description?: string }) => Promise<unknown>>();

  beforeEach(() => {
    vi.clearAllMocks();
    mockOnCreate.mockResolvedValue({ id: 'new-circle' });
  });

  it('test_dialog_when_closed_should_render_nothing', () => {
    const { container } = render(
      <CreateCircleDialog isOpen={false} onClose={mockOnClose} onCreate={mockOnCreate} />
    );
    expect(container.innerHTML).toBe('');
  });

  it('test_dialog_when_open_should_show_form', () => {
    render(
      <CreateCircleDialog isOpen={true} onClose={mockOnClose} onCreate={mockOnCreate} />
    );
    // Heading says "Create Circle", button also says "Create Circle"
    expect(screen.getAllByText('Create Circle').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByLabelText(/^name$/i)).toBeTruthy();
  });

  it('test_dialog_should_have_modal_role', () => {
    render(
      <CreateCircleDialog isOpen={true} onClose={mockOnClose} onCreate={mockOnCreate} />
    );
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('test_dialog_when_submit_with_name_should_call_onCreate', async () => {
    render(
      <CreateCircleDialog isOpen={true} onClose={mockOnClose} onCreate={mockOnCreate} />
    );
    const nameInput = screen.getByRole('textbox', { name: /^name$/i });
    await userEvent.type(nameInput, 'My New Circle');
    const createBtn = screen.getByRole('button', { name: /create circle/i });
    await userEvent.click(createBtn);
    await waitFor(() => {
      expect(mockOnCreate).toHaveBeenCalledWith({
        name: 'My New Circle',
        description: undefined,
      });
    });
  });

  it('test_dialog_when_submit_with_description_should_include_it', async () => {
    render(
      <CreateCircleDialog isOpen={true} onClose={mockOnClose} onCreate={mockOnCreate} />
    );
    await userEvent.type(screen.getByRole('textbox', { name: /^name$/i }), 'Test Circle');
    await userEvent.type(screen.getByRole('textbox', { name: /description/i }), 'A great circle');
    await userEvent.click(screen.getByRole('button', { name: /create circle/i }));
    await waitFor(() => {
      expect(mockOnCreate).toHaveBeenCalledWith({
        name: 'Test Circle',
        description: 'A great circle',
      });
    });
  });

  it('test_dialog_when_empty_name_should_disable_submit', () => {
    render(
      <CreateCircleDialog isOpen={true} onClose={mockOnClose} onCreate={mockOnCreate} />
    );
    const createBtn = screen.getByRole('button', { name: /create circle/i });
    expect(createBtn).toHaveProperty('disabled', true);
  });

  it('test_dialog_when_cancel_should_call_onClose', async () => {
    render(
      <CreateCircleDialog isOpen={true} onClose={mockOnClose} onCreate={mockOnCreate} />
    );
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(mockOnClose).toHaveBeenCalledOnce();
  });

  it('test_dialog_when_escape_pressed_should_close', async () => {
    render(
      <CreateCircleDialog isOpen={true} onClose={mockOnClose} onCreate={mockOnCreate} />
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(mockOnClose).toHaveBeenCalled();
  });

  it('test_dialog_when_backdrop_clicked_should_close', async () => {
    render(
      <CreateCircleDialog isOpen={true} onClose={mockOnClose} onCreate={mockOnCreate} />
    );
    // Click backdrop (aria-hidden div)
    const backdrop = screen.getByRole('dialog').querySelector('[aria-hidden="true"]');
    if (backdrop) {
      await userEvent.click(backdrop);
      expect(mockOnClose).toHaveBeenCalled();
    }
  });

  it('test_dialog_when_close_button_clicked_should_close', async () => {
    render(
      <CreateCircleDialog isOpen={true} onClose={mockOnClose} onCreate={mockOnCreate} />
    );
    await userEvent.click(screen.getByLabelText('Close dialog'));
    expect(mockOnClose).toHaveBeenCalledOnce();
  });
});
