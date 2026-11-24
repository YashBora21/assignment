import React, { useState, useMemo } from 'react';
import { ChevronDown, ChevronRight, AlertCircle, FileText, Link2, HelpCircle, Layers } from 'lucide-react';

const GROUPS = {
  unlinked_requirement: {
    key: 'unlinked_requirements',
    title: 'Unlinked requirements',
    icon: Link2,
  },
  attachment_failure: {
    key: 'attachment_failures',
    title: 'Attachment observations',
    icon: FileText,
  },
  unresolved_standard: {
    key: 'unresolved_standards',
    title: 'Unresolved standard references',
    icon: HelpCircle,
  },
  boq_mismatch: {
    key: 'boq_spec_mismatches',
    title: 'BOQ & specification discrepancies',
    icon: Layers,
  },
  general: {
    key: 'general_notes',
    title: 'General notes',
    icon: AlertCircle,
  },
};

function describeNote(note) {
  // Structured notes carry {type, message, ...}. Plain strings are general.
  if (note && typeof note === 'object') {
    const type = GROUPS[note.type] ? note.type : 'general';
    return { type, text: String(note.message || '').trim(), raw: note };
  }
  return { type: 'general', text: String(note || '').trim(), raw: note };
}

export default function ReviewNotesPanel({ result, reconciliation }) {
  const [isOpen, setIsOpen] = useState(false);

  const groupedNotes = useMemo(() => {
    const groups = {
      unlinked_requirements: [],
      attachment_failures: [],
      unresolved_standards: [],
      boq_spec_mismatches: [],
      general_notes: [],
    };

    const structured = [
      ...(Array.isArray(result?.review_notes) ? result.review_notes : []),
      ...(Array.isArray(reconciliation?.review_notes) ? reconciliation.review_notes : []),
    ];
    const plain = [
      ...(Array.isArray(result?.notes) ? result.notes : []),
      ...(Array.isArray(reconciliation?.notes) ? reconciliation.notes : []),
      ...(Array.isArray(result?.warnings) ? result.warnings : []),
    ];

    const seen = new Set();
    [...structured, ...plain].forEach((note) => {
      const { type, text } = describeNote(note);
      if (!text || seen.has(text)) return;
      seen.add(text);
      groups[GROUPS[type].key].push({ text, raw: typeof note === 'object' ? note : null });
    });

    return groups;
  }, [result, reconciliation]);

  const totalCount = Object.values(groupedNotes).reduce((sum, list) => sum + list.length, 0);

  if (totalCount === 0) return null;

  return (
    <div style={{
      borderBottom: '1px solid var(--hairline)',
      background: 'var(--surface-card)',
      fontSize: '13px',
    }}>
      <div
        onClick={() => setIsOpen(!isOpen)}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '10px 40px',
          cursor: 'pointer',
          userSelect: 'none',
          background: isOpen ? 'rgba(0,0,0,0.02)' : 'transparent',
          transition: 'background 0.2s ease',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          <span style={{ fontWeight: 600, color: 'var(--text-ink)' }}>Review notes</span>
          <span style={{
            fontSize: '11px',
            fontWeight: 700,
            background: 'var(--amber-light, #fef3c7)',
            color: 'var(--amber-dark, #b45309)',
            padding: '2px 8px',
            borderRadius: '999px',
          }}>
            {totalCount}
          </span>
          <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>
            (Non-blocking observations — matching still runs)
          </span>
        </div>
        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          {isOpen ? 'Click to collapse' : 'Click to view'}
        </span>
      </div>

      {isOpen && (
        <div style={{ padding: '12px 40px 16px 40px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {Object.entries(GROUPS).map(([type, group]) => {
            const entries = groupedNotes[group.key];
            if (!entries.length) return null;
            const Icon = group.icon;
            return (
              <div key={type}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 600, color: 'var(--text-ink)', marginBottom: '4px' }}>
                  <Icon size={14} /> {group.title} ({entries.length})
                </div>
                <ul style={{ margin: '0 0 0 20px', padding: 0, color: 'var(--text-muted)' }}>
                  {entries.map((entry, idx) => (
                    <li key={idx}>
                      {entry.text}
                      {Array.isArray(entry.raw?.values) && entry.raw.values.length > 0 && (
                        <ul style={{ margin: '2px 0 0 16px', padding: 0, fontSize: '12px' }}>
                          {entry.raw.values.map((value, valueIdx) => (
                            <li key={valueIdx}>
                              <strong>{value.source_type || 'source'}</strong>
                              {': '}
                              {[value.value, value.unit].filter(Boolean).join(' ') || '—'}
                              {value.document ? ` — ${value.document}` : ''}
                              {value.page ? ` p.${value.page}` : ''}
                            </li>
                          ))}
                        </ul>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
