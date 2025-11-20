import cv2

class TextRenderer:
    @classmethod
    def draw_text(cls, image, text, position, font_scale=0.6, color=(255, 255, 255), thickness=2):
        cv2.putText(image, text, position, cv2.FONT_HERSHEY_SIMPLEX, float(font_scale), color, int(thickness))
        return image