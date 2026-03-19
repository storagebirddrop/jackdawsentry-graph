export type GraphInteractionMode = 'move' | 'grab';

export type GraphViewMode = 'hybrid' | 'entities' | 'activity';

export interface GraphAppearanceState {
  interactionMode: GraphInteractionMode;
  viewMode: GraphViewMode;
  showGrid: boolean;
  showMiniMap: boolean;
  showValues: boolean;
  amountsInFiat: boolean;
  showTxDate: boolean;
  showTxTime: boolean;
  useChainColors: boolean;
  showEntityIcons: boolean;
}

export const DEFAULT_GRAPH_APPEARANCE: GraphAppearanceState = {
  interactionMode: 'move',
  viewMode: 'hybrid',
  showGrid: true,
  showMiniMap: true,
  showValues: true,
  amountsInFiat: false,
  showTxDate: true,
  showTxTime: false,
  useChainColors: true,
  showEntityIcons: true,
};
