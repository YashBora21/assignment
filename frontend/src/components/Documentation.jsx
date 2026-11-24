import React, { useState } from 'react';
import { 
  BookOpen, 
  Cpu, 
  Target, 
  FileText, 
  ChevronRight, 
  ArrowLeft, 
  Terminal, 
  ShieldCheck, 
  Zap, 
  Database, 
  Layers, 
  CheckCircle2 
} from 'lucide-react';

const DOCS_PAGES = [
  {
    id: 'overview',
    title: 'System Overview',
    icon: <BookOpen size={20} />,
    content: (
      <div className="doc-page">
        <h1>System Overview</h1>
        <p className="lead">Tender Matching is an enterprise intelligence platform designed for end-to-end extraction, cross-document reconciliation, and manufacturer catalogue matching for complex procurement tenders.</p>
        
        <div className="doc-section">
          <h3>Architectural Philosophy</h3>
          <p>Unlike standard text scrapers, the platform treats tenders and manufacturer catalogues as high-dimensional structured entities. Every tender passes through a multi-stage deterministic and AI pipeline:</p>
          <ul className="doc-list">
            <li><strong>GeM & Document Ingestion:</strong> Deep extraction of GeM tender wrappers, BOQ schedules, and technical specification attachments using PyMuPDF and PaddleOCR.</li>
            <li><strong>Cross-Document Reconciliation:</strong> Three-way reconciliation (Wrapper vs. BOQ vs. Specifications) resolving quantities, units, and item scopes with non-blocking review notes.</li>
            <li><strong>Vector Retrieval & Qdrant Engine:</strong> High-performance vector search in Qdrant server across pre-indexed manufacturer catalogues with 1024-dim BGE-M3 embeddings.</li>
            <li><strong>Category-Scoped Matching:</strong> Evidence-grounded 4-status evaluation (<span className="text-success">Full Match</span>, <span className="text-warning">Partial Match</span>, <span className="text-danger">No Match</span>, <span className="text-muted">No Evidence</span>) with verbatim audit citations.</li>
          </ul>
        </div>

        <div className="doc-grid">
          <div className="doc-card-small">
            <ShieldCheck size={24} color="var(--success, #16a34a)" />
            <h4>Grounded Evidence</h4>
            <p>Every match decision cites the exact document, page number, and verbatim excerpt from the manufacturer datasheet.</p>
          </div>
          <div className="doc-card-small">
            <Zap size={24} color="#d97706" />
            <h4>High-Speed Vector Search</h4>
            <p>Batched query encoding and HNSW graph indexing in Qdrant Docker server deliver sub-second catalogue lookups.</p>
          </div>
        </div>
      </div>
    )
  },
  {
    id: 'extraction',
    title: 'Ingestion & Extraction',
    icon: <Cpu size={20} />,
    content: (
      <div className="doc-page">
        <h1>Ingestion & Extraction Engine</h1>
        <p className="lead">Multi-modal ingestion pipeline capable of parsing digital PDFs, scanned drawings, tables, and GeM attachment bundles.</p>

        <div className="doc-section">
          <h3>Stage 1: Multi-Modal Ingestion</h3>
          <p>Documents are ingested using PyMuPDF C-bindings for vector text layers. For scanned certificates and drawings, <strong>PaddleOCR</strong> provides high-accuracy text and layout detection with automatic CPU/GPU acceleration fallback. Table boundaries and data cells are parsed into structured schemas to preserve column-row constraints.</p>
        </div>

        <div className="doc-section">
          <h3>Stage 2: GeM Attachment Bundles</h3>
          <p>Tenders from the Government e-Marketplace (GeM) often split procurement data across separate attachments. The engine automatically downloads, caches, and separates:</p>
          <ul className="doc-list">
            <li><strong>Wrapper:</strong> Procurement bid conditions, total quantities, and submission deadlines.</li>
            <li><strong>BOQ Schedules:</strong> Authoritative item codes, descriptions, and line-item quantities.</li>
            <li><strong>Technical Specifications:</strong> Detailed engineering parameters, operating thresholds, and material standards.</li>
          </ul>
        </div>

        <div className="doc-section">
          <h3>Stage 3: LLM Reasoning & Extraction</h3>
          <p>High-throughput cloud inference via Groq (<code>openai/gpt-oss-120b</code>) extracts technical parameters, tolerances, and scopes into strict JSON schemas with 100% verbatim source grounding.</p>
        </div>
      </div>
    )
  },
  {
    id: 'retrieval',
    title: 'Vector Database & Retrieval',
    icon: <Database size={20} />,
    content: (
      <div className="doc-page">
        <h1>Qdrant Vector Database & Hybrid Retrieval</h1>
        <p className="lead">Persistent, server-grade vector search architecture storing tens of thousands of pre-embedded manufacturer catalogue chunks.</p>

        <div className="doc-section">
          <h3>Qdrant Docker Server</h3>
          <p>The vector store runs as a dedicated service (<code>qdrant/qdrant:v1.13.0</code>) on port <code>6333</code> with RocksDB persistent volume storage, native HNSW indexing, and payload filtering.</p>
          <div className="code-block" style={{ background: '#0c0a09', color: '#22d3ee' }}>
            <Terminal size={14} />
            <span>Qdrant Server: http://localhost:6333 | Volume: ./qdrant_storage</span>
          </div>
        </div>

        <div className="doc-section">
          <h3>Embeddings & Payload Indexes</h3>
          <p>Chunks are embedded using the 1024-dimensional multilingual <code>BAAI/bge-m3</code> model. Collections feature native keyword indexes on:</p>
          <ul className="doc-list">
            <li><code>manufacturer</code>: Fast isolation of specific vendor catalogues.</li>
            <li><code>category</code>: Immediate scoping to <code>motors</code>, <code>pumps</code>, or <code>valves</code>.</li>
            <li><code>page</code> & <code>table_id</code>: Rapid citation verification and page-level lookups.</li>
          </ul>
        </div>

        <div className="doc-section">
          <h3>Hybrid Fusion (Dense + BM25)</h3>
          <p>Queries are evaluated through Reciprocal Rank Fusion (RRF, k=60), combining dense semantic vector search with BM25 keyword matching so specific model codes and standards (e.g., "SS316", "PN16", "IS:12615") are never missed.</p>
        </div>
      </div>
    )
  },
  {
    id: 'matching',
    title: 'Manufacturer Match Matrix',
    icon: <Layers size={20} />,
    content: (
      <div className="doc-page">
        <h1>Manufacturer Match Matrix</h1>
        <p className="lead">The matching engine cross-references tender requirements against manufacturer catalogues and generates an evidence-grounded Match Matrix.</p>

        <div className="doc-section">
          <h3>The 4-Status Match Contract</h3>
          <ul className="doc-list">
            <li><strong>Full Match (100%):</strong> The catalogue explicitly provides or exceeds the required technical parameter.</li>
            <li><strong>Partial Match (50%):</strong> The capability is within 20% tolerance or partially satisfies the requirement.</li>
            <li><strong>No Match (0%):</strong> The datasheet contains proven conflicting evidence (contradicts tender requirement).</li>
            <li><strong>No Evidence:</strong> The manufacturer catalogue does not mention this parameter. Absence of evidence is never scored as a failure.</li>
          </ul>
        </div>

        <div className="doc-section">
          <h3>Understanding Compatibility vs. Coverage</h3>
          <p>The system evaluates manufacturers along two distinct axes:</p>
          <ul className="doc-list">
            <li><strong>Compatibility Score:</strong> Compliance rate on specifications that were actually found in the catalogue (e.g., 100% means zero failed checks).</li>
            <li><strong>Coverage Score:</strong> Percentage of the tender's requirements that are documented in the catalogue. When coverage is under 50%, it is marked <code>(Low)</code> to notify the buyer that unverified items require vendor clarification.</li>
          </ul>
        </div>

        <div className="doc-section">
          <h3>Supported Categories & Catalogues</h3>
          <ul className="doc-list">
            <li><strong>Valves:</strong> AVK, Bray, CIRCOR, delval-flow, Duncan Engineering, Emerson Fisher, Flowserve, Intervalve Poonawalla, Valmatic.</li>
            <li><strong>Motors:</strong> ABB, Bharat Bijlee, Crompton Greaves.</li>
            <li><strong>Pumps:</strong> Ebara, KSB, Sulzer, Xylem Godwin, Xylem Goulds.</li>
          </ul>
        </div>
      </div>
    )
  },
  {
    id: 'reconciliation',
    title: 'Reconciliation & Company Fit',
    icon: <Target size={20} />,
    content: (
      <div className="doc-page">
        <h1>Reconciliation & Company Fit</h1>
        <p className="lead">Cross-document conflict resolution and strategic organizational alignment.</p>

        <div className="doc-section">
          <h3>Three-Way Reconciliation</h3>
          <p>Tenders frequently have conflicting quantities or terms between the summary wrapper, BOQ schedules, and technical annexures. The reconciliation engine tracks:</p>
          <ul className="doc-list">
            <li><strong>Consistent:</strong> Parameters agreeing across all documents.</li>
            <li><strong>Conflict:</strong> Explicit discrepancies flagged with page citations from both sides.</li>
            <li><strong>Refines / Additional:</strong> Specifications that add deeper engineering detail to high-level BOQ items.</li>
          </ul>
        </div>

        <div className="doc-section">
          <h3>Company Strategic Fit</h3>
          <p>Evaluates the tender's overall viability against your organizational profile:</p>
          <ul className="doc-list">
            <li><strong>P0 (Direct Fit):</strong> &gt;80% alignment. Core capabilities match with low technical risk.</li>
            <li><strong>P1 (Target):</strong> 60–80% alignment. Good fit, but may require sub-vendor partnerships.</li>
            <li><strong>P2 (Opportunistic):</strong> &lt;60% alignment. High complexity or substantial parameter deviations.</li>
          </ul>
        </div>
      </div>
    )
  }
];

export default function Documentation({ onBack }) {
  const [activePageId, setActivePageId] = useState('overview');
  
  const activePage = DOCS_PAGES.find(p => p.id === activePageId) || DOCS_PAGES[0];

  return (
    <div className="docs-container animate-fade">
      <aside className="docs-sidebar">
        <button className="docs-back" onClick={onBack}>
          <ArrowLeft size={18} />
          Back to Platform
        </button>
        
        <div className="docs-nav">
          <div className="docs-nav-label">Documentation</div>
          {DOCS_PAGES.map(page => (
            <button 
              key={page.id}
              className={`docs-nav-item ${activePageId === page.id ? 'active' : ''}`}
              onClick={() => setActivePageId(page.id)}
            >
              {page.icon}
              {page.title}
              <ChevronRight size={14} className="chevron" />
            </button>
          ))}
        </div>

        <div className="docs-footer">
          <div className="version-tag">TenderExtractPro v2.4.0</div>
          <p>© 2026 Procurement Intelligence</p>
        </div>
      </aside>

      <main className="docs-content">
        <div className="docs-inner animate-fade" key={activePageId}>
          {activePage.content}
        </div>
      </main>
    </div>
  );
}
