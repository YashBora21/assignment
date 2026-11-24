import React, { useRef, useState } from 'react';
import axios from 'axios';
import { Upload, Loader2 } from 'lucide-react';

const ACCEPTED_EXTENSIONS = ['pdf', 'docx', 'jpg', 'jpeg', 'png'];
const MAX_SIZE_MB = 100;

function extensionOf(name) {
    return String(name || '').split('.').pop().toLowerCase();
}

export default function UploadZone({ onUploadComplete }) {
    const [isDragging, setIsDragging] = useState(false);
    const [isUploading, setIsUploading] = useState(false);
    const fileInputRef = useRef(null);

    const handleFile = async (file) => {
        if (!file || isUploading) return;

        const ext = extensionOf(file.name);
        if (!ACCEPTED_EXTENSIONS.includes(ext)) {
            alert(`Unsupported file type ".${ext}". Allowed: ${ACCEPTED_EXTENSIONS.join(', ')}.`);
            return;
        }
        if (file.size > MAX_SIZE_MB * 1024 * 1024) {
            alert(`File is too large. The limit is ${MAX_SIZE_MB} MB.`);
            return;
        }

        const formData = new FormData();
        formData.append('file', file);

        setIsUploading(true);
        try {
            const res = await axios.post('/api/upload', formData, {
                headers: { 'Content-Type': 'multipart/form-data' },
            });
            const data = res.data || {};
            if (data.error || data.detail || !data.job_id) {
                throw new Error(data.detail || data.error || 'Upload failed');
            }
            onUploadComplete({
                job_id: data.job_id,
                filename: data.filename || file.name,
                status: 'queued',
                progress: 0,
                message: 'Queued',
                created_at: Date.now() / 1000,
                warnings: [],
            });
        } catch (err) {
            const detail =
                err?.response?.data?.detail ||
                err?.response?.data?.error ||
                err.message ||
                'Upload failed';
            alert(`Upload failed: ${detail}`);
        } finally {
            setIsUploading(false);
        }
    };

    const onDrop = (e) => {
        e.preventDefault();
        setIsDragging(false);
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            handleFile(e.dataTransfer.files[0]);
        }
    };

    return (
        <div
            className={`upload-zone ${isDragging ? 'drag-active' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
            onDragLeave={(e) => { e.preventDefault(); setIsDragging(false); }}
            onDrop={onDrop}
            onClick={() => { if (!isUploading) fileInputRef.current.click(); }}
            id="sidebar-upload-trigger"
            aria-busy={isUploading}
            style={isUploading ? { opacity: 0.75, cursor: 'wait' } : undefined}
        >
            <input
                type="file"
                accept=".pdf,.docx,.jpg,.jpeg,.png"
                ref={fileInputRef}
                style={{ display: 'none' }}
                onChange={(e) => {
                    if (e.target.files.length > 0) handleFile(e.target.files[0]);
                    e.target.value = '';
                }}
            />
            {isUploading ? (
                <>
                    <Loader2 size={20} className="upload-icon" />
                    <p>Uploading…</p>
                    <span style={{ fontSize: '11px', color: 'var(--text-muted-soft)', display: 'block', marginTop: '4px' }}>
                        Streaming to the server
                    </span>
                </>
            ) : (
                <>
                    <Upload size={20} className="upload-icon" />
                    <p>Upload Tender Document</p>
                    <span style={{ fontSize: '11px', color: 'var(--text-muted-soft)', display: 'block', marginTop: '4px' }}>
                        PDF, DOCX, JPG, PNG · max {MAX_SIZE_MB} MB
                    </span>
                </>
            )}
        </div>
    );
}
