"""Official dimensions must use the same H×W×D convention as existing models."""
import pytest
from fetch_mecan import _extract

@pytest.mark.parametrize(('axes','values','expected'),[
 ('闊x高x深','450 x 350 x 640','350×450×640'),
 ('高x闊x深','350 x 450 x 640','350×450×640'),
])
def test_official_size_axis_normalisation(axes,values,expected):
 html=f'<title>MC07VXH</title>型號 MC07VXH 尺寸({axes}) 毫米 {values} 淨重 公斤 27 冷凍能力 BTU / h 7,500 能源標籤級別 1 雪種 R32'
 assert _extract(html,'MC07VXH')['size']==expected

def test_unknown_official_axes_are_not_guessed():
 html='<title>MC07VXH</title>型號 MC07VXH 尺寸(未確認) 毫米 450 x 350 x 640 淨重 公斤 27 冷凍能力 BTU / h 7,500 能源標籤級別 1 雪種 R32'
 with pytest.raises(ValueError,match='axes'):_extract(html,'MC07VXH')
