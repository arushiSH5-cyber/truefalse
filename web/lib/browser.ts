/** Copy that also works on http://<laptop-ip>, where navigator.clipboard doesn't exist. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const box = document.createElement("textarea");
    box.value = text;
    document.body.append(box);
    box.select();
    const ok = document.execCommand("copy");
    box.remove();
    return ok;
  }
}

const SHRINK_OVER = 8 * 1024 * 1024;
const LONG_EDGE = 3200;

/** Big phone photos are shrunk; smaller ones are sent untouched so AI-detection keeps metadata. */
export async function fitPhoto(file: File): Promise<File> {
  if (file.size <= SHRINK_OVER || !/^image\/(jpeg|png|webp)$/.test(file.type)) return file;
  try {
    const img = await createImageBitmap(file, { imageOrientation: "from-image" });
    // Tall screenshots keep their width, or the text becomes unreadable.
    const tall = img.height > 2.5 * img.width;
    const scale = Math.min(1, tall ? 1568 / img.width : LONG_EDGE / Math.max(img.width, img.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(img.width * scale);
    canvas.height = Math.round(img.height * scale);
    canvas.getContext("2d")?.drawImage(img, 0, 0, canvas.width, canvas.height);
    img.close();
    const blob = await new Promise<Blob | null>((done) => canvas.toBlob(done, "image/jpeg", 0.88));
    return blob ? new File([blob], "photo.jpg", { type: "image/jpeg" }) : file;
  } catch {
    return file;
  }
}

export const isWebUrl = (url: string) => /^https?:\/\//i.test(url);
