from pathlib import Path

from historical_works_discovery import classify_media, file_matches, normalise


def test_normalise_ignores_case_and_spaces():
    assert normalise('ACT LIVE') == normalise('actlive')


def test_file_matches_japanese_alias():
    path = Path('/Volumes/Trancend/KIO/2015/井筒/井筒_企画書.pdf')
    assert '井筒' in file_matches(path, ['井筒', 'Izutsu'])


def test_file_matches_english_alias():
    path = Path('/Volumes/Trancend/KIO/2014/ANEMOS Calling for my wind/photo.jpg')
    matches = file_matches(path, ['ANEMOS', 'Calling for my wind'])
    assert 'ANEMOS' in matches
    assert 'Calling for my wind' in matches


def test_classify_media():
    config = {
        'media_extensions': ['.jpg', '.mov'],
        'content_extensions': ['.pdf', '.docx'],
    }
    assert classify_media(Path('a.jpg'), config) == 'media'
    assert classify_media(Path('a.pdf'), config) == 'document'
    assert classify_media(Path('a.bin'), config) == 'other'
