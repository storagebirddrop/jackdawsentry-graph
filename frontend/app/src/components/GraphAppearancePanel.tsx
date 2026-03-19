import {
  DEFAULT_GRAPH_APPEARANCE,
  type GraphAppearanceState,
  type GraphInteractionMode,
  type GraphViewMode,
} from './graphAppearance';

interface Props {
  appearance: GraphAppearanceState;
  visible: boolean;
  onClose: () => void;
  onChange: (appearance: GraphAppearanceState) => void;
}

export default function GraphAppearancePanel({ appearance, visible, onClose, onChange }: Props) {
  if (!visible) return null;

  function update<K extends keyof GraphAppearanceState>(key: K, value: GraphAppearanceState[K]) {
    onChange({ ...appearance, [key]: value });
  }

  return (
    <div style={panelStyle}>
      <div style={headerStyle}>
        <div>
          <div style={eyebrowStyle}>Canvas</div>
          <div style={titleStyle}>Appearance</div>
        </div>
        <button onClick={onClose} style={closeButtonStyle} aria-label="Close appearance panel">
          x
        </button>
      </div>

      <Section label="Interaction">
        <SegmentedRow<GraphInteractionMode>
          value={appearance.interactionMode}
          options={[
            { value: 'move', label: 'Move' },
            { value: 'grab', label: 'Grab' },
          ]}
          onChange={(value) => update('interactionMode', value)}
        />
      </Section>

      <Section label="View">
        <SegmentedRow<GraphViewMode>
          value={appearance.viewMode}
          options={[
            { value: 'hybrid', label: 'Hybrid' },
            { value: 'entities', label: 'Entities' },
            { value: 'activity', label: 'Activity' },
          ]}
          onChange={(value) => update('viewMode', value)}
        />
      </Section>

      <Section label="Canvas">
        <Toggle
          label="Show grid"
          checked={appearance.showGrid}
          onChange={(checked) => update('showGrid', checked)}
        />
        <Toggle
          label="Show minimap"
          checked={appearance.showMiniMap}
          onChange={(checked) => update('showMiniMap', checked)}
        />
      </Section>

      <Section label="Transaction values">
        <Toggle
          label="Show values"
          checked={appearance.showValues}
          onChange={(checked) => update('showValues', checked)}
        />
        <Toggle
          label="Amounts in fiat"
          checked={appearance.amountsInFiat}
          disabled={!appearance.showValues}
          onChange={(checked) => update('amountsInFiat', checked)}
        />
        <Toggle
          label="Show TX date"
          checked={appearance.showTxDate}
          onChange={(checked) => update('showTxDate', checked)}
        />
        <Toggle
          label="Show time"
          checked={appearance.showTxTime}
          disabled={!appearance.showTxDate}
          onChange={(checked) => update('showTxTime', checked)}
        />
      </Section>

      <Section label="Semantics">
        <Toggle
          label="Use chain colors"
          checked={appearance.useChainColors}
          onChange={(checked) => update('useChainColors', checked)}
        />
        <Toggle
          label="Entity type icons"
          checked={appearance.showEntityIcons}
          onChange={(checked) => update('showEntityIcons', checked)}
        />
      </Section>

      <div style={{ marginTop: 16 }}>
        <button
          onClick={() => onChange(DEFAULT_GRAPH_APPEARANCE)}
          style={resetButtonStyle}
        >
          Reset appearance
        </button>
      </div>
    </div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 14 }}>
      <div style={sectionLabelStyle}>{label}</div>
      <div style={{ display: 'grid', gap: 8 }}>{children}</div>
    </div>
  );
}

function Toggle({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label
      style={{
        ...toggleRowStyle,
        opacity: disabled ? 0.55 : 1,
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
    >
      <span>{label}</span>
      <button
        type="button"
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        style={{
          ...toggleButtonStyle,
          justifyContent: checked ? 'flex-end' : 'flex-start',
          background: checked ? '#2563eb' : '#94a3b8',
        }}
        aria-pressed={checked}
      >
        <span style={toggleThumbStyle} />
      </button>
    </label>
  );
}

function SegmentedRow<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (value: T) => void;
}) {
  return (
    <div style={segmentedRowStyle}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          style={{
            ...segmentStyle,
            background: option.value === value ? '#dbeafe' : 'transparent',
            color: option.value === value ? '#1d4ed8' : '#334155',
          }}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

const panelStyle: React.CSSProperties = {
  position: 'absolute',
  top: 60,
  left: 16,
  zIndex: 110,
  width: 280,
  background: 'rgba(255,255,255,0.96)',
  border: '1px solid rgba(148, 163, 184, 0.35)',
  borderRadius: 18,
  padding: 18,
  color: '#0f172a',
  boxShadow: '0 20px 48px rgba(15, 23, 42, 0.18)',
  backdropFilter: 'blur(14px)',
  fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
};

const headerStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'flex-start',
  justifyContent: 'space-between',
  gap: 12,
};

const eyebrowStyle: React.CSSProperties = {
  color: '#64748b',
  fontSize: 11,
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
  fontWeight: 700,
};

const titleStyle: React.CSSProperties = {
  color: '#0f172a',
  fontSize: 20,
  fontWeight: 700,
  marginTop: 4,
};

const closeButtonStyle: React.CSSProperties = {
  background: 'transparent',
  border: 'none',
  color: '#64748b',
  fontSize: 18,
  lineHeight: 1,
  cursor: 'pointer',
};

const sectionLabelStyle: React.CSSProperties = {
  color: '#64748b',
  fontSize: 12,
  fontWeight: 700,
  marginBottom: 8,
};

const toggleRowStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 16,
  color: '#0f172a',
  fontSize: 14,
};

const toggleButtonStyle: React.CSSProperties = {
  width: 44,
  height: 24,
  border: 'none',
  borderRadius: 999,
  padding: 3,
  display: 'flex',
  alignItems: 'center',
  cursor: 'pointer',
  transition: 'all 0.18s ease',
};

const toggleThumbStyle: React.CSSProperties = {
  width: 18,
  height: 18,
  borderRadius: '50%',
  background: '#ffffff',
  boxShadow: '0 1px 2px rgba(15, 23, 42, 0.2)',
};

const segmentedRowStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
  gap: 6,
  padding: 4,
  background: '#e2e8f0',
  borderRadius: 12,
};

const segmentStyle: React.CSSProperties = {
  padding: '7px 8px',
  border: 'none',
  borderRadius: 10,
  fontSize: 12,
  fontWeight: 700,
  cursor: 'pointer',
};

const resetButtonStyle: React.CSSProperties = {
  width: '100%',
  border: '1px solid #cbd5e1',
  background: '#f8fafc',
  color: '#334155',
  borderRadius: 12,
  padding: '10px 12px',
  fontSize: 13,
  fontWeight: 700,
  cursor: 'pointer',
};
