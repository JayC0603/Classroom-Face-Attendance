import {cleanup, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, expect, it, vi} from 'vitest';
import * as api from '../../api/classes';
import {AdminFaceReviewQueue} from './AdminFaceReviewQueue';

vi.mock('../../api/classes', async importOriginal => ({
  ...await importOriginal<typeof import('../../api/classes')>(),
  listAdminPendingFaceReviews: vi.fn(),
  reviewAdminFaceImage: vi.fn(),
}));

const pending = {
  id: 'image-1', person_id: 'person-1', original_filename: 'dat-phi.jpg', mime_type: 'image/jpeg' as const,
  width: 640, height: 480, review_state: 'PENDING_REVIEW' as const, source: 'MANUAL_UPLOAD' as const,
  created_at: '2026-10-20T00:00:00Z', content_url: '/api/v1/persons/person-1/face-images/image-1/content',
  person_name: 'Đạt Phi', student_id: 'CE181456', person_number: 23,
};

afterEach(() => {cleanup(); vi.resetAllMocks();});

it('lets an admin approve an uploaded sample and removes it from the pending queue', async () => {
  vi.mocked(api.listAdminPendingFaceReviews).mockResolvedValue([pending]);
  vi.mocked(api.reviewAdminFaceImage).mockResolvedValue({...pending, review_state: 'APPROVED'});
  const user = userEvent.setup();
  render(<AdminFaceReviewQueue />);

  expect(await screen.findByAltText('Ảnh chờ duyệt: dat-phi.jpg')).toBeTruthy();
  expect(screen.getByText(/Đạt Phi · ID 23 · CE181456/)).toBeTruthy();
  await user.click(screen.getByRole('button', {name: 'Duyệt ảnh'}));

  await waitFor(() => expect(api.reviewAdminFaceImage).toHaveBeenCalledWith('image-1', 'APPROVE'));
  expect(await screen.findByText('Đã duyệt ảnh. Ảnh đủ chất lượng sẽ được nạp vào so sánh tự động.')).toBeTruthy();
  expect(screen.queryByAltText('Ảnh chờ duyệt: dat-phi.jpg')).toBeNull();
});
