#pragma once

struct RenderInfo;
struct Rep;
struct PyMOLGlobals;
struct CSetting;

struct CCGORenderer {
  PyMOLGlobals* G = nullptr;
  RenderInfo* info = nullptr;
  Rep* rep = nullptr;
  const float* color = nullptr;
  float alpha{};
  short sphere_quality{};
  bool isPicking{};
  bool markerPickable{};
  unsigned pick_pass() const noexcept;
  bool use_shader{}; // OpenGL 1.4+, e.g., glEnableVertexAttribArray() (on) vs.
                     // glEnableClientState() (off)
  bool debug{};
  CSetting* set1 = nullptr;
  CSetting* set2 = nullptr;
  // Metal impostor path: the cylinder `a_cap` flags are usually supplied as a
  // constant generic vertex attribute via a CGO_VERTEX_ATTRIBUTE_1F op (which
  // the GL path applies with glVertexAttrib1f). We capture that constant here
  // so the Metal cylinder draw can pass it as a uniform. Default =
  // cCylShaderBothCapsRound (0x0F).
  float metalCylCapConst = 15.0f;
  // Metal impostor path: the cylinder shader's `uni_radius` uniform. Sticks
  // bake their physical radius into attr_radius and leave this 0, but
  // measurement dashes (distances/H-bonds, dihedrals, angles) bake a 1.0
  // placeholder into attr_radius and deliver the real radius via the
  // CYLINDER_WIDTH_FOR_DISTANCES special op (which on GL sets uni_radius).
  // With no GL shader on Metal we capture it here so the draw can scale
  // attr_radius. Reset to 0 when the cylinder shader is (re)enabled.
  float metalCylUniRadius = 0.0f;
  // Metal interior-cap path: true while the SURFACE shader is the active shader,
  // so the indexed-mesh draw can tell a (closed, cappable) surface apart from a
  // cartoon (both are stride-44 lit triangle meshes). Set at shader-enable.
  bool metalIsSurfaceShader = false;
};

bool CGORendererInit(PyMOLGlobals* G);
void CGORendererFree(PyMOLGlobals* G);
