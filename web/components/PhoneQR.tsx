import Doodle from "@/components/Doodle";

const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

/** On a computer, the demo offers itself to the phone in your hand. Hidden on phones. */
export default function PhoneQR() {
  return (
    <section className="card qr">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={`${BASE}/qr.svg`} width={132} height={132} alt="QR code that opens this site" />
      <div>
        <h2 className="receipt-label row">
          <Doodle name="phone" size={30} />
          Try it on your phone
        </h2>
        <p className="small">Scan with your camera, then add it to your home screen.</p>
      </div>
    </section>
  );
}
