type ApiErrorPayload = {
  error?: string;
  detail?: string;
  message?: string;
  issues?: string[];
};

export class ApiError extends Error {
  status: number;
  errorCode: string;
  issues: string[];
  /** тело ответа целиком: у частичного отказа (507) в нём лежит `saved_count` —
   *  сколько файлов пачки всё-таки сохранено. Без него ошибка сообщала бы «файлы не
   *  загружены» про файлы, которые лежат на месте. */
  payload: Record<string, unknown>;

  constructor(
    message: string,
    options: { status: number; errorCode?: string; issues?: string[]; payload?: Record<string, unknown> },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.errorCode = String(options.errorCode || "").trim();
    this.issues = Array.isArray(options.issues) ? options.issues.filter((item) => String(item || "").trim()) : [];
    this.payload = options.payload || {};
  }
}

async function extractErrorPayload(response: Response): Promise<ApiErrorPayload> {
  try {
    return (await response.json()) as ApiErrorPayload;
  } catch {
    return {};
  }
}

function buildApiErrorMessage(response: Response, payload: ApiErrorPayload): string {
  if (typeof payload.detail === "string" && payload.detail.trim()) {
    return payload.detail.trim();
  }
  if (typeof payload.message === "string" && payload.message.trim()) {
    return payload.message.trim();
  }
  if (typeof payload.error === "string" && payload.error.trim()) {
    return payload.error.trim();
  }
  return `API ${response.status}`;
}

async function throwApiError(response: Response): Promise<never> {
  const payload = await extractErrorPayload(response);
  if (response.status === 401 && typeof window !== "undefined") {
    const loginPath = "/app/login";
    if (window.location.pathname !== loginPath) {
      window.location.assign(loginPath);
    }
  }
  throw new ApiError(buildApiErrorMessage(response, payload), {
    status: response.status,
    errorCode: payload.error,
    issues: payload.issues,
    // тело целиком: отказ вроде «переименование сменит код книги» несёт в нём цифры,
    // без которых вопрос человеку («204 файла перестанут находиться») не задать
    payload: payload as Record<string, unknown>,
  });
}

export function describeApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.errorCode === "publish_gate" && error.issues.length > 0) {
      return `Publish gate блокирует approve: ${error.issues.join("; ")}.`;
    }
    if (error.message.trim()) {
      return error.message.trim();
    }
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message.trim();
  }
  return fallback;
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: {
      Accept: "application/json",
    },
  });
  if (!response.ok) {
    await throwApiError(response);
  }
  return (await response.json()) as T;
}

export async function apiPostJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    await throwApiError(response);
  }
  return (await response.json()) as T;
}

export async function apiPatchJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "PATCH",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    await throwApiError(response);
  }
  return (await response.json()) as T;
}

export async function apiDelete<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    method: "DELETE",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    await throwApiError(response);
  }
  return (await response.json()) as T;
}

export async function apiPostForm<T>(path: string, formData: FormData): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json" },
    body: formData,
  });
  if (!response.ok) {
    await throwApiError(response);
  }
  return (await response.json()) as T;
}

/**
 * Multipart POST with upload progress (XHR — fetch has no upload events).
 * Same cookies, same error shape as apiPostForm.
 */
export function apiUploadForm<T>(
  path: string,
  formData: FormData,
  onProgress?: (fraction: number) => void,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path, true);
    xhr.withCredentials = true;
    xhr.responseType = "text";
    xhr.setRequestHeader("Accept", "application/json");
    if (xhr.upload && onProgress) {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total);
      };
    }
    xhr.onerror = () => reject(new ApiError("Сеть недоступна", { status: 0 }));
    xhr.onabort = () => reject(new ApiError("Загрузка прервана", { status: 0 }));
    xhr.onload = () => {
      let payload: ApiErrorPayload & Record<string, unknown> = {};
      try {
        payload = JSON.parse(xhr.responseText || "{}");
      } catch {
        payload = {};
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload as T);
        return;
      }
      if (xhr.status === 401 && typeof window !== "undefined" && window.location.pathname !== "/app/login") {
        window.location.assign("/app/login");
      }
      reject(
        new ApiError(buildApiErrorMessage({ status: xhr.status } as Response, payload), {
          status: xhr.status,
          errorCode: payload.error,
          issues: payload.issues,
          payload,
        }),
      );
    };
    xhr.send(formData);
  });
}
