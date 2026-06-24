import cv2
import numpy as np

VIDEO_PATH = r"C:\Users\hendr\PycharmProjects\sumocutter\videos\Natsu_2019_Juryo_-_Day_2.mp4"

# CHANGE THIS to a timestamp (in seconds) where the overlay is clearly visible on screen!
# e.g., 5 minutes in = 300.0
TIME_IN_SECONDS = 789.0

def calibrate():
    cap = cv2.VideoCapture(VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(TIME_IN_SECONDS * fps))
    ret, frame = cap.read()
    cap.release()

    if not ret:
        print("Error: Could not read frame at that timestamp.")
        return

    print("\n--- INSTRUCTIONS ---")
    print("1. A window will open showing the video frame.")
    print("2. Use your mouse to click and drag a box around REGION 1A (the colored box next to the first name).")
    print("3. Press SPACE or ENTER to confirm.")
    print("4. A second window will open. Click and drag a box around REGION 1B (the colored box next to the second name).")
    print("5. Press SPACE or ENTER to confirm.")
    print("(If you mess up dragging, press 'c' to cancel and redraw the box)\n")

    # Let user select Region 1A visually
    r1a = cv2.selectROI("Select Region 1A (First Name Box)", frame, fromCenter=False, showCrosshair=True)
    cv2.destroyWindow("Select Region 1A (First Name Box)")
    x1_a, y1_a, w_a, h_a = r1a
    crop_a = frame[y1_a:y1_a+h_a, x1_a:x1_a+w_a]

    # Let user select Region 1B visually
    r1b = cv2.selectROI("Select Region 1B (Second Name Box)", frame, fromCenter=False, showCrosshair=True)
    cv2.destroyWindow("Select Region 1B (Second Name Box)")
    x1_b, y1_b, w_b, h_b = r1b
    crop_b = frame[y1_b:y1_b+h_b, x1_b:x1_b+w_b]

    # Calculate actual OpenCV HSV means for exactly what you drew
    hsv_a = cv2.cvtColor(crop_a, cv2.COLOR_BGR2HSV).mean(axis=(0,1))
    hsv_b = cv2.cvtColor(crop_b, cv2.COLOR_BGR2HSV).mean(axis=(0,1))

    print("\n" + "="*60)
    print("SUCCESS! COPY AND PASTE THIS EXACTLY INTO YOUR MAIN SCRIPT:")
    print("="*60)
    print(f"REGION1A_LOSSES = ({x1_a}, {y1_a}, {x1_a+w_a}, {y1_a+h_a})")
    print(f"REGION1B_WINS   = ({x1_b}, {y1_b}, {x1_b+w_b}, {y1_b+h_b})\n")

    print(f"REGION1A_HSV_TARGET    = ({int(hsv_a[0])}, {int(hsv_a[1])}, {int(hsv_a[2])})")
    print(f"REGION1A_HSV_TOLERANCE = (15, 60, 60)\n")

    print(f"REGION1B_HSV_TARGET    = ({int(hsv_b[0])}, {int(hsv_b[1])}, {int(hsv_b[2])})")
    print(f"REGION1B_HSV_TOLERANCE = (15, 60, 60)")
    print("="*60)

if __name__ == "__main__":
    calibrate()