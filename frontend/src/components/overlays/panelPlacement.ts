import { createContext, useContext } from 'react';

export type ResponsivePanelPlacement =
  | 'standalone'
  | 'rail'
  | 'drawer'
  | 'sheet';

export const ResponsivePanelPlacementContext =
  createContext<ResponsivePanelPlacement>('standalone');

export function useResponsivePanelPlacement(): ResponsivePanelPlacement {
  return useContext(ResponsivePanelPlacementContext);
}
