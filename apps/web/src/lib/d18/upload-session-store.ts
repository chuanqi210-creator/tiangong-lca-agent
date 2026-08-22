import type { PersistedUploadTransfer } from "./upload-manager.ts";

const DATABASE = "d18.material-upload-sessions.v1";
const STORE = "sessions";

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: "uploadId" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function saveUploadSession(session: PersistedUploadTransfer): Promise<void> {
  const db = await database();
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction(STORE, "readwrite");
    transaction.objectStore(STORE).put(session); // Metadata only. Raw bytes are never persisted here.
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
  db.close();
}

export async function loadUploadSessions(): Promise<PersistedUploadTransfer[]> {
  const db = await database();
  const sessions = await new Promise<PersistedUploadTransfer[]>((resolve, reject) => {
    const request = db.transaction(STORE).objectStore(STORE).getAll();
    request.onsuccess = () => resolve(request.result as PersistedUploadTransfer[]);
    request.onerror = () => reject(request.error);
  });
  db.close();
  return sessions.map((session) => ({ ...session, status: "reselect_required" }));
}

export async function deleteUploadSession(uploadId: string): Promise<void> {
  const db = await database();
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction(STORE, "readwrite");
    transaction.objectStore(STORE).delete(uploadId);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
  db.close();
}
