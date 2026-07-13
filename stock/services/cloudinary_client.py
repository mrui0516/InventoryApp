"""Thin wrapper over the Cloudinary SDK for mirroring product images.

Account is in dynamic-folders mode: ``public_id`` is the full identifier (no
folder prefix); ``asset_folder`` only organizes the Media Library. The delivery
URL is ``.../image/upload/<public_id>.<ext>``.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class CloudinaryError(Exception):
    """Any failure talking to Cloudinary."""


class CloudinaryClient:
    def is_configured(self):
        return bool(
            getattr(settings, 'CLOUDINARY_CLOUD_NAME', '')
            and getattr(settings, 'CLOUDINARY_API_KEY', '')
            and getattr(settings, 'CLOUDINARY_API_SECRET', '')
        )

    def _configure(self):
        import cloudinary
        cloudinary.config(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            secure=True,
        )

    def upload_image(self, filepath, public_id, asset_folder):
        import cloudinary.uploader
        self._configure()
        try:
            result = cloudinary.uploader.upload(
                filepath,
                public_id=public_id,
                asset_folder=asset_folder,
                resource_type='image',
                unique_filename=False,
                overwrite=True,
                invalidate=True,
            )
        except Exception as exc:  # SDK raises various types
            raise CloudinaryError(f'upload failed for {public_id}: {exc}') from exc
        return result.get('secure_url')

    def delete_image(self, public_id):
        import cloudinary.uploader
        self._configure()
        try:
            cloudinary.uploader.destroy(public_id, resource_type='image', invalidate=True)
        except Exception as exc:
            raise CloudinaryError(f'delete failed for {public_id}: {exc}') from exc
