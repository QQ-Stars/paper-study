export type WorkspaceAnnouncement = {
  kind: 'stage' | 'complete' | 'recoverable-error';
  message: string;
};

export type AnnouncementListener = (
  announcement: WorkspaceAnnouncement,
) => void;

const listeners = new Set<AnnouncementListener>();

export function announceWorkspace(announcement: WorkspaceAnnouncement): void {
  const message = announcement.message.trim();
  if (!message) {
    return;
  }

  for (const listener of listeners) {
    listener({ ...announcement, message });
  }
}

export function subscribeToWorkspaceAnnouncements(
  listener: AnnouncementListener,
): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
