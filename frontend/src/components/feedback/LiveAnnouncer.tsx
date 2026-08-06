import { useEffect, useRef, useState } from 'react';

import {
  subscribeToWorkspaceAnnouncements,
  type AnnouncementListener,
} from './announcements';

interface LiveAnnouncerProps {
  throttleMs?: number;
}

export function LiveAnnouncer({ throttleMs = 160 }: LiveAnnouncerProps) {
  const [message, setMessage] = useState('');
  const timerRef = useRef<number | null>(null);
  const pendingRef = useRef('');

  useEffect(() => {
    const clearTimer = () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };

    const listener: AnnouncementListener = (announcement) => {
      if (announcement.kind !== 'stage') {
        clearTimer();
        pendingRef.current = '';
        setMessage(announcement.message);
        return;
      }

      pendingRef.current = announcement.message;
      clearTimer();
      timerRef.current = window.setTimeout(() => {
        setMessage(pendingRef.current);
        pendingRef.current = '';
        timerRef.current = null;
      }, throttleMs);
    };

    const unsubscribe = subscribeToWorkspaceAnnouncements(listener);
    return () => {
      unsubscribe();
      clearTimer();
    };
  }, [throttleMs]);

  return (
    <div
      className="visually-hidden"
      role="status"
      aria-atomic="true"
      aria-live="polite"
    >
      {message}
    </div>
  );
}
