#include "ReShade.fxh"

uniform float FreedeckCrtScanlineStrength <
    ui_type = "slider";
    ui_label = "Scanline Strength";
    ui_min = 0.0;
    ui_max = 0.45;
    ui_step = 0.01;
> = 0.18;

uniform float FreedeckCrtCurvature <
    ui_type = "slider";
    ui_label = "Screen Curvature";
    ui_min = 0.0;
    ui_max = 0.18;
    ui_step = 0.005;
> = 0.055;

uniform float FreedeckCrtVignette <
    ui_type = "slider";
    ui_label = "Corner Vignette";
    ui_min = 0.0;
    ui_max = 0.45;
    ui_step = 0.01;
> = 0.14;

uniform float FreedeckCrtBrightness <
    ui_type = "slider";
    ui_label = "Brightness Boost";
    ui_min = 0.8;
    ui_max = 1.3;
    ui_step = 0.01;
> = 1.03;

float4 PS_FreedeckCrtPrototype(float4 position : SV_Position, float2 texcoord : TEXCOORD) : SV_Target
{
    float2 centered = texcoord * 2.0 - 1.0;
    float radius_sq = dot(centered, centered);
    float2 warped = centered + centered * (radius_sq * FreedeckCrtCurvature);
    float2 sample_uv = warped * 0.5 + 0.5;

    if (sample_uv.x < 0.0 || sample_uv.x > 1.0 || sample_uv.y < 0.0 || sample_uv.y > 1.0)
    {
        return float4(0.0, 0.0, 0.0, 1.0);
    }

    float4 color = tex2D(ReShade::BackBuffer, sample_uv);
    float scanline_wave = 0.5 + 0.5 * sin(sample_uv.y * BUFFER_HEIGHT * 1.5 * 3.14159265);
    float scanline_mix = 1.0 - FreedeckCrtScanlineStrength * (1.0 - scanline_wave);
    float vignette = 1.0 - FreedeckCrtVignette * pow(saturate(radius_sq), 1.45);

    color.rgb *= scanline_mix * vignette * FreedeckCrtBrightness;
    color.rgb = saturate(color.rgb);
    return color;
}

technique FreedeckCRTPrototype
{
    pass
    {
        VertexShader = PostProcessVS;
        PixelShader = PS_FreedeckCrtPrototype;
    }
}
