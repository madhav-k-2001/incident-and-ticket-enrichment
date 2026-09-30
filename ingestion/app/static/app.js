document.addEventListener("DOMContentLoaded", () => {
  // Elements
  const dropArea = document.getElementById("drop-area");
  const fileInput = document.getElementById("file-elem");
  const stagedList = document.getElementById("staged-files-list");
  const stagedItems = document.getElementById("staged-items");
  const stagedCount = document.getElementById("staged-count");
  const clearStagedBtn = document.getElementById("clear-staged-btn");
  const uploadBtn = document.getElementById("upload-btn");
  const documentsTbody = document.getElementById("documents-tbody");
  const refreshDocsBtn = document.getElementById("refresh-docs-btn");
  const searchForm = document.getElementById("search-form");
  const searchQuery = document.getElementById("search-query");
  const topKSlider = document.getElementById("top-k-slider");
  const topKDisplay = document.getElementById("top-k-display");
  const filterDoc = document.getElementById("filter-doc");
  const searchResultsContainer = document.getElementById("search-results-container");
  const searchBtn = document.getElementById("search-btn");

  // Quota meters
  const tpmVal = document.getElementById("tpm-val");
  const tpmFill = document.getElementById("tpm-fill");
  const rpmVal = document.getElementById("rpm-val");
  const rpmFill = document.getElementById("rpm-fill");
  const rpdVal = document.getElementById("rpd-val");
  const rpdFill = document.getElementById("rpd-fill");
  const healthBadge = document.getElementById("system-health-badge");

  // Modal
  const chunkModal = document.getElementById("chunk-modal");
  const modalBackdrop = document.getElementById("modal-backdrop");
  const modalCloseBtn = document.getElementById("modal-close-btn");
  const modalDocTitle = document.getElementById("modal-doc-title");
  const modalDocSubtitle = document.getElementById("modal-doc-subtitle");
  const modalBody = document.getElementById("modal-body");

  let stagedFiles = [];
  let pollInterval = null;

  // Setup Drag & Drop
  ["dragenter", "dragover"].forEach(eventName => {
    dropArea.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropArea.classList.add("drag-over");
    }, false);
  });

  ["dragleave", "drop"].forEach(eventName => {
    dropArea.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropArea.classList.remove("drag-over");
    }, false);
  });

  dropArea.addEventListener("drop", (e) => {
    const dt = e.dataTransfer;
    const files = dt.files;
    handleFilesSelected(files);
  });

  fileInput.addEventListener("change", (e) => {
    handleFilesSelected(e.target.files);
  });

  function handleFilesSelected(files) {
    for (const file of files) {
      const ext = file.name.split(".").pop().toLowerCase();
      if (["pdf", "docx", "doc"].includes(ext)) {
        if (!stagedFiles.some(f => f.name === file.name && f.size === file.size)) {
          stagedFiles.push(file);
        }
      } else {
        alert(`File "${file.name}" is not supported. Please upload PDF or DOCX files.`);
      }
    }
    renderStagedFiles();
  }

  function renderStagedFiles() {
    if (stagedFiles.length === 0) {
      stagedList.classList.add("hidden");
      fileInput.value = "";
      return;
    }

    stagedList.classList.remove("hidden");
    stagedCount.textContent = `${stagedFiles.length} file(s) selected`;
    stagedItems.innerHTML = "";

    stagedFiles.forEach((file, index) => {
      const li = document.createElement("li");
      li.className = "staged-item";
      const sizeMb = (file.size / (1024 * 1024)).toFixed(2);
      const ext = file.name.split(".").pop().toUpperCase();
      
      li.innerHTML = `
        <div class="staged-item-info">
          <span class="file-badge badge-${ext.toLowerCase()}">${ext}</span>
          <span>${file.name} (${sizeMb} MB)</span>
        </div>
        <button class="remove-file-btn" data-index="${index}" title="Remove">&times;</button>
      `;
      stagedItems.appendChild(li);
    });

    document.querySelectorAll(".remove-file-btn").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const idx = parseInt(e.target.getAttribute("data-index"), 10);
        stagedFiles.splice(idx, 1);
        renderStagedFiles();
      });
    });
  }

  clearStagedBtn.addEventListener("click", () => {
    stagedFiles = [];
    renderStagedFiles();
  });

  // Upload handler
  uploadBtn.addEventListener("click", async () => {
    if (stagedFiles.length === 0) return;

    uploadBtn.disabled = true;
    uploadBtn.innerHTML = `Uploading ${stagedFiles.length} file(s)...`;

    const formData = new FormData();
    stagedFiles.forEach(file => {
      formData.append("files", file);
    });

    try {
      const res = await fetch("/api/upload", {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(errorData.detail || "Upload failed");
      }

      const data = await res.json();
      stagedFiles = [];
      renderStagedFiles();
      fetchDocuments();
      startPolling();
    } catch (err) {
      alert(`Upload error: ${err.message}`);
    } finally {
      uploadBtn.disabled = false;
      uploadBtn.innerHTML = `
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path>
        </svg>
        Enqueue for Ingestion
      `;
    }
  });

  // Fetch Documents
  async function fetchDocuments() {
    try {
      const res = await fetch("/api/documents");
      if (!res.ok) return;
      const data = await res.json();
      renderDocumentsTable(data.documents || []);
      updateFilterDropdown(data.documents || []);

      // If active processing, ensure polling is fast
      const hasActive = (data.documents || []).some(d => 
        ["PENDING", "PARSING", "PROCESSING", "RATE_LIMITED_PAUSED"].includes(d.status)
      );
      if (!hasActive && pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
    } catch (e) {
      console.error("Error fetching documents:", e);
    }
  }

  function renderDocumentsTable(docs) {
    if (docs.length === 0) {
      documentsTbody.innerHTML = `
        <tr>
          <td colspan="5" class="empty-state">No documents uploaded yet. Drop files above to start!</td>
        </tr>
      `;
      return;
    }

    documentsTbody.innerHTML = "";
    docs.forEach(doc => {
      const tr = document.createElement("tr");
      const sizeKb = (doc.file_size / 1024).toFixed(1);
      const isComplete = doc.status === "COMPLETED";

      let statusClass = `status-${doc.status}`;
      let statusLabel = doc.status;
      if (doc.status === "RATE_LIMITED_PAUSED") {
        statusLabel = "PAUSED (Quota)";
      } else if (doc.status === "FAILED") {
        statusLabel = "FAILED (View)";
      }

      const retryBtnHtml = (doc.status === "FAILED")
        ? `<button class="btn btn-primary btn-sm retry-btn" data-id="${doc.id}" title="Retry Ingestion">Retry</button>`
        : (doc.status === "PROCESSING" || doc.status === "PARSING" || doc.status === "RATE_LIMITED_PAUSED")
        ? `<button class="btn btn-secondary btn-sm retry-btn" data-id="${doc.id}" title="Force re-queue if stuck">Re-queue</button>`
        : "";

      const errorAttr = doc.error_message ? `title="${escapeHtml(doc.error_message)}" style="cursor: pointer;"` : "";

      tr.innerHTML = `
        <td>
          <div class="doc-name-cell">
            <span class="file-badge badge-${doc.file_type}">${doc.file_type}</span>
            <span title="${doc.filename}">${doc.filename}</span>
          </div>
        </td>
        <td>${sizeKb} KB</td>
        <td>
          <span class="status-pill ${statusClass} ${doc.status === 'FAILED' ? 'clickable-status' : ''}" ${errorAttr} data-error="${escapeHtml(doc.error_message || '')}">
            <span class="status-dot"></span> ${statusLabel}
          </span>
        </td>
        <td>
          <div class="table-progress-box">
            <div class="progress-bar-bg">
              <div class="progress-bar-fill" style="width: ${doc.progress_percent}%;"></div>
            </div>
            <div class="table-progress-text">
              ${doc.processed_chunks} / ${doc.total_chunks || 0} chunks (${doc.progress_percent}%)
            </div>
          </div>
        </td>
        <td>
          <div class="table-actions">
            ${retryBtnHtml}
            <button class="btn btn-secondary btn-sm inspect-btn" data-id="${doc.id}" data-name="${doc.filename}" title="Inspect Extracted Chunks">
              Inspect
            </button>
            <button class="btn btn-danger btn-sm delete-btn" data-id="${doc.id}" title="Delete Document">
              &times;
            </button>
          </div>
        </td>
      `;
      documentsTbody.appendChild(tr);
    });

    // Attach click listeners to failed status pills
    document.querySelectorAll(".clickable-status").forEach(pill => {
      pill.addEventListener("click", () => {
        const errMsg = pill.getAttribute("data-error");
        if (errMsg) {
          alert(`Document Failure Reason:\n\n${errMsg}`);
        }
      });
    });

    // Attach retry / re-queue listeners
    document.querySelectorAll(".retry-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-id");
        btn.disabled = true;
        btn.textContent = "Re-queueing...";
        try {
          const res = await fetch(`/api/documents/${id}/retry`, { method: "POST" });
          if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || "Failed to retry document.");
          }
          fetchDocuments();
          startPolling();
        } catch (e) {
          alert(`Retry failed: ${e.message}`);
          btn.disabled = false;
          btn.textContent = "Retry";
        }
      });
    });

    // Attach inspect chunk listeners
    document.querySelectorAll(".inspect-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-id");
        const name = btn.getAttribute("data-name");
        openChunkModal(id, name);
      });
    });

    // Attach delete listeners
    document.querySelectorAll(".delete-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-id");
        if (confirm("Delete this document and all its vector embeddings?")) {
          await fetch(`/api/documents/${id}`, { method: "DELETE" });
          fetchDocuments();
        }
      });
    });
  }

  function updateFilterDropdown(docs) {
    const currentVal = filterDoc.value;
    filterDoc.innerHTML = `<option value="">All Ingested Documents</option>`;
    docs.filter(d => d.status === "COMPLETED").forEach(d => {
      const opt = document.createElement("option");
      opt.value = d.id;
      opt.textContent = d.filename;
      if (d.id === currentVal) opt.selected = true;
      filterDoc.appendChild(opt);
    });
  }

  // Quota & Health Check
  async function fetchQuotaAndHealth() {
    try {
      const [quotaRes, healthRes] = await Promise.all([
        fetch("/api/quota"),
        fetch("/api/health")
      ]);

      if (quotaRes.ok) {
        const q = await quotaRes.json();
        if (q.tpm) {
          tpmVal.textContent = `${q.tpm.used.toLocaleString()} / 30,000`;
          tpmFill.style.width = `${Math.min(100, (q.tpm.used / 30000) * 100)}%`;
          tpmFill.classList.toggle("warning", q.tpm.used > 24000);
        }
        if (q.rpm) {
          rpmVal.textContent = `${q.rpm.used} / 100`;
          rpmFill.style.width = `${Math.min(100, (q.rpm.used / 100) * 100)}%`;
          rpmFill.classList.toggle("warning", q.rpm.used > 80);
        }
        if (q.rpd) {
          rpdVal.textContent = `${q.rpd.used} / 1,000`;
          rpdFill.style.width = `${Math.min(100, (q.rpd.used / 1000) * 100)}%`;
          rpdFill.classList.toggle("warning", q.rpd.used > 950);
        }
      }

      if (healthRes.ok) {
        const h = await healthRes.json();
        if (h.status === "healthy") {
          healthBadge.className = "badge badge-health";
          healthBadge.innerHTML = `<span class="status-dot"></span> System Ready (${h.embedding_model})`;
        } else {
          healthBadge.className = "badge status-RATE_LIMITED_PAUSED";
          healthBadge.innerHTML = `<span class="status-dot"></span> Services Connecting...`;
        }
      }
    } catch (e) {
      console.error("Quota/health fetch error:", e);
    }
  }

  function startPolling() {
    if (!pollInterval) {
      pollInterval = setInterval(() => {
        fetchDocuments();
        fetchQuotaAndHealth();
      }, 2500);
    }
  }

  refreshDocsBtn.addEventListener("click", () => {
    fetchDocuments();
    fetchQuotaAndHealth();
  });

  // Slider
  topKSlider.addEventListener("input", (e) => {
    topKDisplay.textContent = e.target.value;
  });

  // Semantic Search
  searchForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const query = searchQuery.value.trim();
    if (!query) return;

    searchBtn.disabled = true;
    searchBtn.textContent = "Embedding & Searching...";
    searchResultsContainer.innerHTML = `<div class="search-placeholder">Searching pgvector index...</div>`;

    try {
      const payload = {
        query: query,
        top_k: parseInt(topKSlider.value, 10),
        document_id: filterDoc.value || null
      };

      const res = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Search failed");
      }

      const data = await res.json();
      renderSearchResults(data);
      fetchQuotaAndHealth(); // Update rate limit meter after query embedding
    } catch (err) {
      searchResultsContainer.innerHTML = `
        <div class="search-placeholder" style="color: var(--accent-rose);">
          Error: ${err.message}
        </div>
      `;
    } finally {
      searchBtn.disabled = false;
      searchBtn.textContent = "Search";
    }
  });

  function renderSearchResults(data) {
    if (!data.results || data.results.length === 0) {
      searchResultsContainer.innerHTML = `
        <div class="search-placeholder">
          No matching vectors found. Make sure documents are fully ingested and completed.
        </div>
      `;
      return;
    }

    searchResultsContainer.innerHTML = "";
    data.results.forEach(item => {
      const card = document.createElement("div");
      card.className = "search-result-item";
      const simPercent = (item.similarity * 100).toFixed(1);
      const pageInfo = item.page_number ? `&bull; Page ${item.page_number}` : "";

      card.innerHTML = `
        <div class="result-header">
          <div class="result-source">
            <span>📄 ${item.filename}</span>
            <span style="color: var(--text-dim);">${pageInfo}</span>
          </div>
          <span class="similarity-badge">${simPercent}% match</span>
        </div>
        <div class="result-content">${escapeHtml(item.content)}</div>
      `;
      searchResultsContainer.appendChild(card);
    });
  }

  // Chunk Modal
  async function openChunkModal(docId, docName) {
    modalDocTitle.textContent = docName;
    modalDocSubtitle.textContent = "Extracted text chunks & pgvector status";
    modalBody.innerHTML = `<div class="loading-spinner">Loading chunks...</div>`;
    chunkModal.classList.remove("hidden");

    try {
      const res = await fetch(`/api/documents/${docId}/chunks?limit=100`);
      if (!res.ok) throw new Error("Could not load chunks.");
      const data = await res.json();

      if (!data.chunks || data.chunks.length === 0) {
        modalBody.innerHTML = `<p class="empty-state">No chunks available yet for this document.</p>`;
        return;
      }

      modalBody.innerHTML = "";
      data.chunks.forEach(c => {
        const div = document.createElement("div");
        div.className = "chunk-card";
        const pageText = c.page_number ? `Page ${c.page_number}` : `Section`;
        div.innerHTML = `
          <div class="chunk-meta">
            <span>Chunk #${c.chunk_index + 1} (${pageText})</span>
            <span>${c.char_count} chars &bull; ~${c.estimated_tokens} tokens</span>
          </div>
          <div class="chunk-text">${escapeHtml(c.content)}</div>
        `;
        modalBody.appendChild(div);
      });
    } catch (e) {
      modalBody.innerHTML = `<p style="color: var(--accent-rose);">Error: ${e.message}</p>`;
    }
  }

  function closeModal() {
    chunkModal.classList.add("hidden");
  }

  modalCloseBtn.addEventListener("click", closeModal);
  modalBackdrop.addEventListener("click", closeModal);

  function escapeHtml(str) {
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // Initial load
  fetchDocuments();
  fetchQuotaAndHealth();
  setInterval(fetchQuotaAndHealth, 5000);
});
